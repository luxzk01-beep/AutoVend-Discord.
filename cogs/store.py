import os
import io
import re
import unicodedata
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

def remove_accents(text: str) -> str:
    nfkd = unicodedata.normalize('NFKD', text)
    return "".join([c for c in nfkd if not unicodedata.combining(c)])

def format_tlv(id_str: str, value: str) -> str:
    return f"{id_str}{len(value):02d}{value}"

def generate_pix_payload(key: str, name: str, city: str, amount: float, identifier: str) -> str:
    name_clean = remove_accents(name)[:25].upper()
    city_clean = remove_accents(city)[:15].upper()
    
    identifier_clean = re.sub(r'[^a-zA-Z0-9]', '', remove_accents(identifier))
    identifier_clean = identifier_clean[:25] if identifier_clean else "***"
    
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

class FeedbackModal(discord.ui.Modal, title="Avalie sua Compra"):
    comment = discord.ui.TextInput(
        label="Deixe um comentário (opcional)",
        style=discord.TextStyle.paragraph,
        placeholder="O que achou do atendimento e do produto?",
        required=False,
        max_length=300
    )

    def __init__(self, order_id: int, rating: int):
        super().__init__()
        self.order_id = order_id
        self.rating = rating

    async def on_submit(self, interaction: discord.Interaction):
        try:
            supabase.table("orders").update({
                "rating": self.rating,
                "feedback_comment": self.comment.value
            }).eq("id", self.order_id).execute()

            stars = "⭐" * self.rating
            await interaction.response.send_message(f"✅ Muito obrigado pelo seu feedback!\nAvaliação: {stars}", ephemeral=True)
            
            res = supabase.table("orders").select("*").eq("id", self.order_id).execute()
            if res.data:
                order = res.data[0]
                
                embed = discord.Embed(
                    title="⭐ Nova Avaliação de Cliente!",
                    color=discord.Color.gold()
                )
                embed.add_field(name="Cliente", value=f"<@{order['buyer_id']}>", inline=True)
                embed.add_field(name="Produto", value=order['product_name'], inline=True)
                embed.add_field(name="Nota", value=stars, inline=False)
                if self.comment.value:
                    embed.add_field(name="Comentário", value=f"_{self.comment.value}_", inline=False)
                embed.set_footer(text=f"Pedido ID: #{order['id']}")

                target_channel = discord.utils.get(interaction.guild.text_channels, name="avaliacoes")
                if not target_channel:
                    target_channel = discord.utils.get(interaction.guild.text_channels, name="feedbacks")
                
                if target_channel:
                    await target_channel.send(embed=embed)
                else:
                    await interaction.channel.send(embed=embed)

            message = interaction.message
            if message:
                await message.edit(view=None)
        except Exception as e:
            await interaction.response.send_message(f"❌ Erro ao salvar avaliação: {e}", ephemeral=True)

class FeedbackView(discord.ui.View):
    def __init__(self, order_id: int):
        super().__init__(timeout=None)
        self.order_id = order_id

    @discord.ui.button(label="⭐ 1", style=discord.ButtonStyle.secondary)
    async def star_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FeedbackModal(self.order_id, 1))

    @discord.ui.button(label="⭐⭐ 2", style=discord.ButtonStyle.secondary)
    async def star_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FeedbackModal(self.order_id, 2))

    @discord.ui.button(label="⭐⭐⭐ 3", style=discord.ButtonStyle.primary)
    async def star_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FeedbackModal(self.order_id, 3))

    @discord.ui.button(label="⭐⭐⭐⭐ 4", style=discord.ButtonStyle.primary)
    async def star_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FeedbackModal(self.order_id, 4))

    @discord.ui.button(label="⭐⭐⭐⭐⭐ 5", style=discord.ButtonStyle.success)
    async def star_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FeedbackModal(self.order_id, 5))

class OrderControlView(discord.ui.View):
    def __init__(self, order_id: int):
        super().__init__(timeout=None)
        self.order_id = order_id

    @discord.ui.button(label="✅ Aprovar Pedido", style=discord.ButtonStyle.success, custom_id="approve_order_btn")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator and not any(role.id == ADMIN_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("❌ Apenas administradores podem aprovar pedidos.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            supabase.table("orders").update({"status": "aprovado"}).eq("id", self.order_id).execute()

            await interaction.followup.send(f"🎉 **Pagamento Aprovado!** Pedido concluído com sucesso.")
            await interaction.channel.send("Obrigado por comprar conosco! Por favor, avalie sua experiência abaixo:", view=FeedbackView(self.order_id))

            for child in self.children:
                child.disabled = True
            await interaction.message.edit(view=self)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao aprovar pedido: {e}")

class ProductSelect(discord.ui.Select):
    def __init__(self, products):
        self.products = products
        options = []
        
        for index, product in enumerate(products):
            label = product['name'][:100]
            desc = f"Preço: R$ {product['price']:.2f}"[:100]
            
            options.append(
                discord.SelectOption(
                    label=label,
                    description=desc,
                    value=str(index)
                )
            )

        super().__init__(
            placeholder="➡️ Clique aqui para ver as opções",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        view: CatalogSelectView = self.view
        view.index = int(self.values[0])
        await interaction.edit_original_response(embed=view.get_embed(), view=view)

class CatalogSelectView(discord.ui.View):
    def __init__(self, products, index=0):
        super().__init__(timeout=None)
        self.products = products
        self.index = index
        self.add_item(ProductSelect(products))

    def get_embed(self):
        if not self.products:
            return discord.Embed(title="Loja Vazia", description="Não há produtos cadastrados.", color=discord.Color.red())
        
        config_res = supabase.table("store_config").select("*").eq("id", 1).execute()
        config = config_res.data[0] if config_res.data else {"banner_url": None, "embed_color": "blue"}
        
        color_map = {
            "black": discord.Color.from_rgb(1, 1, 1),
            "blue": discord.Color.blue(),
            "green": discord.Color.green(),
            "red": discord.Color.red(),
            "gold": discord.Color.gold(),
            "purple": discord.Color.purple()
        }
        embed_color = color_map.get(config.get("embed_color", "blue"), discord.Color.blue())

        product = self.products[self.index]
        embed = discord.Embed(
            title=product['name'],
            description=product['description'],
            color=embed_color
        )
        embed.add_field(name="Preço", value=f"**R$ {product['price']:.2f}**", inline=False)
        
        img_url = product.get('image_url') or config.get('banner_url')
        if img_url and isinstance(img_url, str) and img_url.startswith("http"):
            try:
                embed.set_image(url=img_url)
            except Exception:
                pass

        embed.set_footer(text=f"Produto {self.index + 1} de {len(self.products)}")
        return embed

    @discord.ui.button(label="Comprar 🛒", style=discord.ButtonStyle.success, row=1)
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True, ephemeral=True)

        if not self.products:
            await interaction.followup.send("❌ Nenhum produto disponível para compra.", ephemeral=True)
            return

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
                name=f"pedido-{order['id']}-{interaction.user.name[:10]}",
                type=discord.ChannelType.private_thread,
                auto_archive_duration=1440
            )
            try:
                await thread.add_user(interaction.user)
            except Exception:
                pass

            pix_code = generate_pix_payload(
                key=PIX_KEY,
                name=PIX_NAME,
                city=PIX_CITY,
                amount=float(product['price']),
                identifier=f"PEDIDO{order['id']}"
            )

            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=4,
            )
            qr.add_data(pix_code)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")

            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            file = discord.File(buf, filename="pix_qr.png")

            pix_embed = discord.Embed(
                title=f"Pedido #{order['id']} Criado com Sucesso!",
                description=f"Olá {interaction.user.mention}, efetue o pagamento escaneando o QR Code abaixo ou usando a opção **Pix Copia e Cola**.",
                color=discord.Color.gold()
            )
            pix_embed.add_field(name="Produto", value=product['name'], inline=True)
            pix_embed.add_field(name="Valor", value=f"R$ {product['price']:.2f}", inline=True)
            pix_embed.add_field(name="Chave PIX (Copia e Cola)", value=f"```{pix_code}```", inline=False)
            pix_embed.set_image(url="attachment://pix_qr.png")

            await interaction.followup.send(f"✅ Pedido criado com sucesso! Veja seu tópico privado: {thread.mention}", ephemeral=True)
            
            control_view = OrderControlView(order['id'])
            await thread.send(embed=pix_embed, file=file, view=control_view)
        except Exception as e:
            error_msg = traceback.format_exc()
            await interaction.followup.send(f"❌ Erro ao processar compra:\n```py\n{error_msg[-1800:]}\n```", ephemeral=True)

class Store(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="loja", description="Envia o catálogo fixo da loja no canal para todos verem (Admin)")
    @is_admin()
    async def loja(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            res = supabase.table("products").select("*").execute()
            products = res.data

            if not products:
                await interaction.followup.send("❌ Não há produtos cadastrados no momento. Use `/adicionar_produto` para cadastrar.")
                return

            view = CatalogSelectView(products)
            await interaction.followup.send(embed=view.get_embed(), view=view)
        except Exception as e:
            error_msg = traceback.format_exc()
            await interaction.followup.send(f"❌ Erro ao enviar a loja:\n```py\n{error_msg[-1800:]}\n```")

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
            await interaction.followup.send(f"❌ Erro ao adicionar produto:\n```py\n{error_msg[-1800:]}\n```", ephemeral=True)

    @app_commands.command(name="set_banner", description="Altera o banner/imagem principal da loja (Admin)")
    @app_commands.describe(url="Link direto da imagem do banner")
    @is_admin()
    async def set_banner(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            supabase.table("store_config").update({"banner_url": url}).eq("id", 1).execute()
            await interaction.followup.send(f"✅ Banner da loja atualizado com sucesso!\n🖼️ **URL:** {url}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao atualizar banner: {e}", ephemeral=True)

    @app_commands.command(name="set_cor", description="Altera a cor do tema da loja (Admin)")
    @app_commands.describe(cor="Escolha a cor do tema")
    @app_commands.choices(cor=[
        app_commands.Choice(name="Preto", value="black"),
        app_commands.Choice(name="Azul", value="blue"),
        app_commands.Choice(name="Verde", value="green"),
        app_commands.Choice(name="Vermelho", value="red"),
        app_commands.Choice(name="Dourado/Ouro", value="gold"),
        app_commands.Choice(name="Roxo", value="purple")
    ])
    @is_admin()
    async def set_cor(self, interaction: discord.Interaction, cor: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            supabase.table("store_config").update({"embed_color": cor}).eq("id", 1).execute()
            await interaction.followup.send(f"✅ Cor do tema da loja alterada para **{cor}**!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao atualizar cor: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Store(bot))
