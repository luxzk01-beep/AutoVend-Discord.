import os
import io
import crcmod
import qrcode
import discord
from discord import app_commands
from discord.ext import commands
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURAÇÕES DE AMBIENTE ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))

PIX_KEY = os.getenv("PIX_KEY", "")
PIX_CITY = os.getenv("PIX_CITY", "BRASILIA")
PIX_NAME = os.getenv("PIX_NAME", "VENDEDOR")

# --- CONEXÃO COM SUPABASE ---
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# --- GERADOR DE PAYLOAD PIX (BR CODE) ---
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


# --- DECORADOR DE PERMISSÃO ADM ---
def is_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        if any(role.id == ADMIN_ROLE_ID for role in getattr(interaction.user, "roles", [])):
            return True
        await interaction.response.send_message("❌ Apenas administradores podem executar este comando.", ephemeral=True)
        return False
    return app_commands.check(predicate)


# --- VIEWS (BOTÕES E INTERFACES) ---
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
        product = self.products[self.index]
        
        # Registra pedido no banco
        res = supabase.table("orders").insert({
            "buyer_id": interaction.user.id,
            "buyer_name": str(interaction.user),
            "product_id": product['id'],
            "product_name": product['name'],
            "price": product['price'],
            "status": "pendente"
        }).execute()
        
        order = res.data[0]

        # Cria Tópico Privado
        thread = await interaction.channel.create_thread(
            name=f"🛒-pedido-{order['id']}-{interaction.user.name}",
            type=discord.ChannelType.private_thread,
            auto_archive_duration=1440
        )
        await thread.add_user(interaction.user)

        # Gera Chave PIX e QR Code
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
        pix_embed.add_field(name="Chave PIX (Copia e Cola)", value=f"```{pix_code}