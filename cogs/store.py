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

# --- FUNÇÕES AUXILIARES ---
def remove_accents(text: str) -> str:
    nfkd = unicodedata.normalize('NFKD', text)
    return "".join([c for c in nfkd if not unicodedata.combining(c)])

def format_tlv(id_str: str, value: str) -> str:
    return f"{id_str}{len(value):02d}{value}"

def generate_pix_payload(key: str, name: str, city: str, amount: float, identifier: str) -> str:
    name_clean = remove_accents(name)[:25].upper()
    city_clean = remove_accents(city)[:15].upper()
    identifier_clean = re.sub(r'[^a-zA-Z0-9]', '', remove_accents(identifier))[:25] or "***"
    amount_str = f"{amount:.2f}"
    payload = format_tlv("00", "01") + format_tlv("01", "12") + format_tlv("26", format_tlv("00", "br.gov.bcb.pix") + format_tlv("01", key)) + format_tlv("52", "0000") + format_tlv("53", "986") + format_tlv("54", amount_str) + format_tlv("55", "55") + format_tlv("58", "BR") + format_tlv("59", name_clean) + format_tlv("60", city_clean) + format_tlv("62", format_tlv("05", identifier_clean)) + "6304"
    crc16_func = crcmod.mkCrcFun(0x11021, initCrc=0xFFFF, rev=False, xorOut=0x0000)
    return payload + hex(crc16_func(payload.encode('utf-8')))[2:].upper().zfill(4)

def is_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator or any(role.id == ADMIN_ROLE_ID for role in getattr(interaction.user, "roles", [])):
            return True
        await interaction.response.send_message("❌ Apenas administradores.", ephemeral=True)
        return False
    return app_commands.check(predicate)

# --- CLASSES DE INTERAÇÃO (VIEWS E MODAIS) ---
class FeedbackModal(discord.ui.Modal, title="Avalie sua Compra"):
    comment = discord.ui.TextInput(label="Comentário", style=discord.TextStyle.paragraph, required=False, max_length=300)
    def __init__(self, order_id, rating): super().__init__(); self.order_id = order_id; self.rating = rating
    async def on_submit(self, interaction: discord.Interaction):
        supabase.table("orders").update({"rating": self.rating, "feedback_comment": self.comment.value}).eq("id", self.order_id).execute()
        await interaction.response.send_message("✅ Obrigado!", ephemeral=True)
        await interaction.message.edit(view=None)

class FeedbackView(discord.ui.View):
    def __init__(self, order_id): super().__init__(timeout=None); self.order_id = order_id
    @discord.ui.button(label="⭐⭐⭐⭐⭐ 5", style=discord.ButtonStyle.success)
    async def star_5(self, interaction: discord.Interaction, button): await interaction.response.send_modal(FeedbackModal(self.order_id, 5))

class OrderControlView(discord.ui.View):
    def __init__(self, order_id): super().__init__(timeout=None); self.order_id = order_id
    @discord.ui.button(label="✅ Aprovar", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button):
        supabase.table("orders").update({"status": "aprovado"}).eq("id", self.order_id).execute()
        await interaction.response.send_message("Pedido aprovado!", ephemeral=True)
        await interaction.channel.send("Obrigado! Avalie:", view=FeedbackView(self.order_id))
        for child in self.children: child.disabled = True
        await interaction.message.edit(view=self)

class TicketView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Abrir Ticket 🎫", style=discord.ButtonStyle.blurple, custom_id="ticket_support_btn")
    async def ticket_button(self, interaction: discord.Interaction, button):
        thread = await interaction.channel.create_thread(name=f"suporte-{interaction.user.name[:10]}", type=discord.ChannelType.private_thread)
        await thread.add_user(interaction.user)
        await thread.send(f"Olá {interaction.user.mention}, em que posso ajudar?")
        await interaction.response.send_message(f"✅ Ticket: {thread.mention}", ephemeral=True)

class CatalogSelectView(discord.ui.View):
    def __init__(self, products, index=0): super().__init__(timeout=None); self.products = products; self.index = index; self.add_item(ProductSelect(products))
    def get_embed(self):
        p = self.products[self.index]
        embed = discord.Embed(title=p['name'], description=p['description'], color=discord.Color.blue())
        embed.add_field(name="Preço", value=f"R$ {p['price']:.2f}")
        if p.get('image_url'): embed.set_image(url=p['image_url'])
        return embed
    @discord.ui.button(label="Comprar 🛒", style=discord.ButtonStyle.success, row=1)
    async def buy(self, interaction: discord.Interaction, button):
        await interaction.response.defer(thinking=True, ephemeral=True)
        p = self.products[self.index]
        o = supabase.table("orders").insert({"buyer_id": interaction.user.id, "product_id": p['id'], "product_name": p['name'], "price": p['price'], "status": "pendente"}).execute().data[0]
        thread = await interaction.channel.create_thread(name=f"pedido-{o['id']}", type=discord.ChannelType.private_thread)
        await thread.add_user(interaction.user)
        pix = generate_pix_payload(PIX_KEY, PIX_NAME, PIX_CITY, float(p['price']), f"PEDIDO{o['id']}")
        await thread.send(f"Pague o PIX:\n```{pix}```", view=OrderControlView(o['id']))
        await interaction.followup.send(f"✅ Criado: {thread.mention}", ephemeral=True)

class ProductSelect(discord.ui.Select):
    def __init__(self, products): options = [discord.SelectOption(label=p['name'][:100], value=str(i)) for i, p in enumerate(products)]; super().__init__(placeholder="Ver produtos", options=options)
    async def callback(self, interaction: discord.Interaction):
        self.view.index = int(self.values[0])
        await interaction.response.edit_message(embed=self.view.get_embed(), view=self.view)

# --- BOT E COMANDOS ---
class Store(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="painel_suporte")
    @is_admin()
    async def painel_suporte(self, interaction: discord.Interaction):
        await interaction.channel.send("❓ **Precisa de ajuda?**", view=TicketView())
        await interaction.response.send_message("Enviado!", ephemeral=True)

    # Adicione aqui abaixo os seus comandos antigos de loja que você já tinha:
    # loja_streaming, loja_pc, loja_bots, adicionar_produto, editar_produto, set_banner, set_cor...

async def setup(bot): await bot.add_cog(Store(bot))
