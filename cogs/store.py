import os
import io
import traceback
import crcmod
import qrcode
import discord
from discord import app_commands
from discord.ext import commands
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))

PIX_KEY = os.getenv("PIX_KEY", "")
PIX_CITY = os.getenv("PIX_CITY", "BRASILIA")
PIX_NAME = os.getenv("PIX_NAME", "VENDEDOR")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def format_tlv(id_str: str, value: str) -> str:
    return f"{id_str}{len(value):02d}{value}"

def generate_pix_payload(key: str, name: str, city: str, amount: float, identifier: str) -> str:
    name_clean = name[:25].upper()
    city_clean = city[:15].upper()
    identifier_clean = "".join(e for e in identifier if e.isalnum())[:25] or "***"
    amount_str = f"{amount:.2f}"

    gui = format_tlv("00", "br.gov.bcb.pix")
    key_tlv = format_tlv("01", key)
    merchant_account = format_tlv("26", gui + key_tlv)

    txid_tlv = format_tlv("05", identifier_clean)
    additional_data = format_tlv("62", txid_tlv)

    payload = (
        format_tlv("00", "01") +
        format_tlv("01", "12") +
        merchant_account +
        format_tlv("52", "0000") +
        format_tlv("53", "986") +
        format_tlv("54", amount_str) +
        format_tlv("55", "55") +
        format_tlv("58", "BR") +
        format_tlv("59", name_clean) +
        format_tlv("60", city_clean) +
        additional_data +
        "6304"
    )

    crc16_func = crcmod.mkCrcFun(0x11021, initCrc=0xFFFF, rev=False, xorOut=0x0000)
    crc_value = hex(crc16_func(payload.encode('utf-8')))[2:].upper().zfill(4)
    
    return payload + crc_value

def is_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        if any(role.id == ADMIN_ROLE_ID for role in getattr(interaction.user, "roles", [])):
            return True
        await interaction.response.send_message("❌ Apenas administradores podem executar este comando.", ephemeral=True)
        return False
    return app_commands.check(predicate)

class CatalogView(discord.ui.View):
    def __init__(self, products, index=0):
        super().__init__(timeout=180)
        self.products = products
        self.index = index

    def get_embed(self):
        product = self.products[self.index]
        embed = discord.Embed(
            title=product['name'],
            description=product['description'],
            color=discord.Color.blue()
        )
        embed.add_field(name="Preço", value=f"**R$ {product['price']:.2f}**", inline=False)
        embed.set_image(url=product['image_url'])
        embed.set_footer(text=f"Produto {self.index + 1} de {len(self.products)}")
        return embed

    @discord.ui.button(label="◀ Anterior", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index - 1) % len(self.products)
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="Comprar 🛒", style=discord.ButtonStyle.success)
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            product = self.products[self.index]
            
            res = supabase.table("orders").insert({
                "buyer_id": interaction.user.id,
                "buyer_name": str(interaction.user),
                "product_id": product['id'],
                "product_name": product['name'],
                "price": product['price'],
                "status": "pendente"
            }).execute()
            
            order = res.data[0]

            thread = await interaction.channel.create_thread(
                name=f"pedido-{order['id']}-{interaction.user.name}",
                type=discord.ChannelType.private_thread,
                auto_archive_duration=1440
            )
            await thread.add_user(interaction.user)

            pix_code = generate_pix_payload(
                key=PIX_KEY,
                name=PIX_NAME,
                city=PIX_CITY,
                amount=product['price'],
                identifier=interaction.user.name
            )

            qr = qrcode.make(pix_code)
            buf = io.BytesIO()
            qr.save(buf, format='PNG')
            buf.seek(0)
            file = discord.File(buf, filename="pix_qr.png")

            pix_embed = discord.Embed(
                title=f"Pedido #{order['id']} Criado com Sucesso!",
                description=f"Olá {interaction.user.mention}, efetue o pagamento para concluir a compra.",
                color=discord.Color.gold()
            )
            pix_embed.add_field(name="Produto", value=product['name'], inline=True)
            pix_embed.add_field(name="Valor", value=f"R$ {product['price']:.2f}", inline=True)
            pix_embed.add_field(name="Chave PIX (Copia e Cola)", value=f"```{pix_code}```", inline=False)
            pix_embed.set_image(url="attachment://pix_qr.png")

            await interaction.followup.send(f"✅ Pedido criado com sucesso! Veja seu tópico privado: {thread.mention}", ephemeral=True)
            await thread.send(embed=pix_embed, file=file)
        except Exception as e:
            error_msg = traceback.format_exc()
            await interaction.followup.send(f"❌ Erro ao processar compra:\n```py\n{error_msg[-1800:]}\n```", ephemeral=True)

    @discord.ui.button(label="Próximo ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index + 1) % len(self.products)
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

class Store(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="loja", description="Mostra o catálogo de produtos disponíveis")
    async def loja(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            res = supabase.table("products").select("*").execute()
            products = res.data

            if not products:
                await interaction.followup.send("❌ Não há produtos cadastrados no momento.", ephemeral=True)
                return

            view = CatalogView(products)
            await interaction.followup.send(embed=view.get_embed(), view=view, ephemeral=True)
        except Exception as e:
            error_msg = traceback.format_exc()
            await interaction.followup.send(f"❌ Erro ao abrir a loja:\n```py\n{error_msg[-1800:]}\n```", ephemeral=True)

    @app_commands.command(name="adicionar_produto", description="Adiciona um novo produto à loja (Admin)")
    @app_commands.describe(name="Nome do produto", description="Descrição", price="Preço em reais", image_url="Link da imagem")
    @is_admin()
    async def adicionar_produto(self, interaction: discord.Interaction, name: str, description: str, price: float, image_url: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            supabase.table("products").insert({
                "name": name,
                "description": description,
                "price": price,
                "image_url": image_url
            }).execute()

            await interaction.followup.send(f"✅ Produto **{name}** adicionado com sucesso!", ephemeral=True)
        except Exception as e:
            error_msg = traceback.format_exc()
            await interaction.followup.send(f"❌ Erro ao adicionar produto:\n```py\n{
