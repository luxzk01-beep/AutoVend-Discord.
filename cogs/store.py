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

                target_channel = discord.utils.get(interaction.guild.text_channels, name="vouchers")
                if not target_channel:
                    target_channel = discord.utils.get(interaction.guild.text_channels, name="avaliacoes")
                if not target_channel:
                    target_channel = discord.utils.get(interaction.guild.text_channels, name="feedbacks")
                if not target_channel:
                    target_channel = discord.utils.find(lambda c: "feedbacks" in c.name or "avaliac" in c.name or "vouchers" in c.name, interaction.guild.text_channels)
                
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

            order_res = supabase.table("orders").select("*").eq("id", self.order_id).execute()
            if order_res.data:
                order = order_res.data[0]
                prod_res = supabase.table("products").select("*").eq("id", order["product_id"]).execute()
                
                if prod_res.data:
                    product = prod_res.data[0]
                    prod_name = product["name"].lower()
                    
                    delivery_info = product.get("delivery_content") or product["description"]
                    is_pc = "pc" in prod_name or "otimiz" in prod_name or "fps" in prod_name

                    if not is_pc:
                        delivery_embed = discord.Embed(
                            title="📦 Produto Entregue com Sucesso!",
                            description=f"Olá <@{order['buyer_id']}>, seu pagamento foi aprovado! Aqui estão os dados do seu produto:\n\n{delivery_info}",
                            color=discord.Color.green()
                        )
                        await interaction.channel.send(embed=delivery_embed)

            await interaction.followup.send(f"🎉 **Pagamento Aprovado!** Pedido concluído com sucesso.")
            
            await interaction.channel.send("Obrigado por comprar conosco! Por favor, avalie sua experiência abaixo:", view=FeedbackView(self.order_id))

            for child in self.children:
                child.disabled = True
            await interaction.message.edit(view=self)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao aprovar pedido: {e}")

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Abrir Ticket de Suporte", style=discord.ButtonStyle.success, emoji="🎫", custom_id="ticket_support_btn")
    async def ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            thread = await interaction.channel.create_thread(
                name=f"suporte-{interaction.user.name[:10]}",
                type=discord.ChannelType.private_thread,
                auto_archive_duration=1440
            )
            
            try:
                await thread.add_user(interaction.user)
            except Exception:
                pass

            try:
                dono_user = await interaction.guild.fetch_member(1472689611917627597)
                if dono_user:
                    await thread.add_user(dono_user)
            except Exception:
                pass

            embed = discord.Embed(
                title="🎫 Ticket de Suporte Aberto",
                description=f"Olá {interaction.user.mention}, descreva sua dúvida ou problema detalhadamente. Nossa equipe responderá em breve!",
                color=discord.Color.green()
            )
            await thread.send(embed=embed)
            await interaction.response.send_message(f"✅ Ticket aberto com sucesso: {thread.mention}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Erro ao abrir ticket: {e}", ephemeral=True)

class EditarProdutoModal(discord.ui.Modal, title="Editar Produto"):
    novo_nome = discord.ui.TextInput(
        label="Nome do Produto",
        placeholder="Digite o novo nome...",
        required=True
    )
    nova_descricao = discord.ui.TextInput(
        label="Descrição do Catálogo",
        style=discord.TextStyle.paragraph,
        placeholder="Texto visível na vitrine...",
        required=True
    )
    novo_delivery = discord.ui.TextInput(
        label="Conteúdo de Entrega Automática",
        style=discord.TextStyle.paragraph,
        placeholder="Link ou dados enviados após aprovar...",
        required=False
    )
    novo_preco = discord.ui.TextInput(
        label="Preço (Ex: 24.99)",
        placeholder="24.99",
        required=True
    )
    nova_imagem = discord.ui.TextInput(
        label="Link da Imagem (URL)",
        placeholder="https://...",
        required=False
    )

    def __init__(self, product_id: int, current_name: str, current_desc: str, current_delivery: str, current_price: float, current_img: str):
        super().__init__()
        self.product_id = product_id
        self.novo_nome.default = current_name
        self.nova_descricao.default = current_desc
        if current_delivery:
            self.novo_delivery.default = current_delivery
        self.novo_preco.default = str(current_price)
        if current_img:
            self.nova_imagem.default = current_img

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            preco_float = float(self.novo_preco.value.replace(",", "."))
            
            update_data = {
                "name": self.novo_nome.value,
                "description": self.nova_descricao.value,
                "delivery_content": self.novo_delivery.value,
                "price": preco_float
            }
            if self.nova_imagem.value:
                update_data["image_url"] = self.nova_imagem.value

            supabase.table("products").update(update_data).eq("id", self.product_id).execute()
            await interaction.followup.send(f"✅ Produto ID `{self.product_id}` atualizado com sucesso!", ephemeral=True)
        except ValueError:
            await interaction.followup.send("❌ O preço informado é inválido! Use números e ponto/vírgula.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao atualizar produto: {e}", ephemeral=True)

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
        config = config_res.data[0] if config_res.data else {"banner_url": None, "embed_color": "black"}
        
        color_map = {
            "black": discord.Color.from_rgb(1, 1, 1),
            "blue": discord.Color.blue(),
            "green": discord.Color.green(),
            "red": discord.Color.red(),
            "gold": discord.Color.gold(),
            "purple": discord.Color.purple()
        }
        embed_color = color_map.get(config.get("embed_color", "black"), discord.Color.from_rgb(1, 1, 1))

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

            try:
                dono_user = await interaction.guild.fetch_member(1472689611917627597)
                if dono_user:
                    await thread.add_user(dono_user)
            except Exception as e:
                print(f"Erro ao adicionar o dono ao tópico: {e}")

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

            prod_name_lower = product['name'].lower()
            is_pc_optimization = "pc" in prod_name_lower or "otimiz" in prod_name_lower or "fps" in prod_name_lower

            pix_embed = discord.Embed(
                title=f"Pedido #{order['id']} Criado com Sucesso!",
                description=f"Olá {interaction.user.mention}, efetue o pagamento escaneando o QR Code abaixo ou usando a opção **Pix Copia e Cola**.",
                color=discord.Color.gold()
            )
            pix_embed.add_field(name="Produto", value=product['name'], inline=True)
            pix_embed.add_field(name="Valor", value=f"R$ {product['price']:.2f}", inline=True)

            if is_pc_optimization:
                pix_embed.add_field(
                    name="💻 Próximo Passo para a Otimização",
                    value="Assim que realizar o pagamento e enviar o comprovante aqui, mande o seu **código do AnyDesk** para iniciarmos o procedimento!",
                    inline=False
                )
            else:
                pix_embed.add_field(
                    name="🤖 Entrega Automática",
                    value="Assim que o pagamento for aprovado, os dados/acessos do produto serão entregues automaticamente aqui no seu tópico!",
                    inline=False
                )

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

    async def cog_load(self):
        try:
            synced = await self.bot.tree.sync()
            print(f"[Store] Comandos sincronizados com sucesso: {len(synced)} comandos.")
        except Exception as e:
            print(f"[Store] Erro ao sincronizar comandos: {e}")

    @app_commands.command(name="painel_suporte", description="Envia o painel profissional de tickets de suporte (Admin)")
    @is_admin()
    async def painel_suporte(self, interaction: discord.Interaction):
        await interaction.response.send_message("Enviando painel de suporte...", ephemeral=True)
        try:
            config_res = supabase.table("store_config").select("*").eq("id", 1).execute()
            config = config_res.data[0] if config_res.data else {"banner_url": None, "embed_color": "black"}
            
            color_map = {
                "black": discord.Color.from_rgb(1, 1, 1),
                "blue": discord.Color.blue(),
                "green": discord.Color.green(),
                "red": discord.Color.red(),
                "gold": discord.Color.gold(),
                "purple": discord.Color.purple()
            }
            embed_color = color_map.get(config.get("embed_color", "black"), discord.Color.blue())

            embed = discord.Embed(
                title="🎫 Central de Atendimento & Suporte",
                description=(
                    "Está com dúvidas, encontrou algum problema ou precisa de ajuda com seu pedido?\n\n"
                    "• Clique no botão verde abaixo para iniciar um **atendimento privado**.\n"
                    "• Nossa equipe será notificada imediatamente.\n\n"
                    "> *Evite abrir tickets sem necessidade.*"
                ),
                color=embed_color
            )
            
            img_url = config.get('banner_url')
            if img_url and isinstance(img_url, str) and img_url.startswith("http"):
                try:
                    embed.set_image(url=img_url)
                except Exception:
                    pass

            embed.set_footer(text="Sistema de Suporte Automatizado", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

            await interaction.channel.send(embed=embed, view=TicketView())
        except Exception as e:
            await interaction.channel.send(f"❌ Erro ao enviar painel de suporte: {e}")

    @app_commands.command(name="editar_painel", description="Edita o título e descrição do painel de suporte mais recente")
    @app_commands.describe(novo_titulo="O novo título do painel", nova_descricao="A nova descrição (use \\n para quebrar linhas)")
    @is_admin()
    async def editar_painel(self, interaction: discord.Interaction, novo_titulo: str, nova_descricao: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            async for message in interaction.channel.history(limit=10):
                if message.author == self.bot.user and message.embeds:
                    embed = message.embeds[0]
                    embed.title = novo_titulo
                    embed.description = nova_descricao.replace("\\n", "\n")
                    
                    await message.edit(embed=embed)
                    await interaction.followup.send("✅ Painel editado com sucesso!", ephemeral=True)
                    return
            
            await interaction.followup.send("❌ Não encontrei nenhum painel (embed) enviado por mim neste canal.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao editar: {e}", ephemeral=True)

    @app_commands.command(name="loja_streaming", description="Envia o catálogo de Streaming (Admin)")
    @is_admin()
    async def loja_streaming(self, interaction: discord.Interaction):
        await interaction.response.send_message("Enviando catálogo de streaming...", ephemeral=True)
        try:
            res = supabase.table("products").select("*").execute()
            products = [p for p in res.data if "streaming" in p.get("name", "").lower() or "netflix" in p.get("name", "").lower() or "disney" in p.get("name", "").lower() or "prime" in p.get("name", "").lower()]
            if not products:
                products = res.data

            view = CatalogSelectView(products)
            await interaction.channel.send(embed=view.get_embed(), view=view)
        except Exception as e:
            await interaction.channel.send(f"❌ Erro ao enviar loja de streaming: {e}")

    @app_commands.command(name="loja_pc", description="Envia o catálogo de Otimização de PC (Admin)")
    @is_admin()
    async def loja_pc(self, interaction: discord.Interaction):
        await interaction.response.send_message("Enviando catálogo de PC...", ephemeral=True)
        try:
            res = supabase.table("products").select("*").execute()
            products = [p for p in res.data if "otimiz" in p.get("name", "").lower() or "fps" in p.get("name", "").lower() or "pc" in p.get("name", "").lower()]
            if not products:
                products = res.data

            view = CatalogSelectView(products)
            await interaction.channel.send(embed=view.get_embed(), view=view)
        except Exception as e:
            await interaction.channel.send(f"❌ Erro ao enviar loja de PC: {e}")

    @app_commands.command(name="loja_bots", description="Envia o catálogo de Bots (Admin)")
    @is_admin()
    async def loja_bots(self, interaction: discord.Interaction):
        await interaction.response.send_message("Enviando catálogo de bots...", ephemeral=True)
        try:
            res = supabase.table("products").select("*").execute()
            products = [p for p in res.data if "bot" in p.get("name", "").lower()]
            if not products:
                products = res.data

            view = CatalogSelectView(products)
            await interaction.channel.send(embed=view.get_embed(), view=view)
        except Exception as e:
            await interaction.channel.send(f"❌ Erro ao enviar loja de bots: {e}")

    @app_commands.command(name="adicionar_produto", description="Adiciona um novo produto à loja (Admin)")
    @app_commands.describe(name="Nome", description="Descrição da vitrine", price="Preço", delivery_content="O que será enviado automaticamente", image_url="Link da imagem")
    @is_admin()
    async def adicionar_produto(self, interaction: discord.Interaction, name: str, description: str, price: float, delivery_content: str, image_url: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            data_to_insert = {
                "name": name,
                "description": description,
                "price": price,
                "delivery_content": delivery_content,
                "image_url": image_url
            }
            supabase.table("products").insert(data_to_insert).execute()
            await interaction.followup.send(f"✅ Produto **{name}** adicionado com sucesso!", ephemeral=True)
        except Exception as e:
            error_msg = traceback.format_exc()
            await interaction.followup.send(f"❌ Erro ao adicionar produto:\n```py\n{error_msg[-1800:]}\n```", ephemeral=True)

    @app_commands.command(name="editar_produto", description="Edita um produto existente informando o ID (Admin)")
    @app_commands.describe(product_id="O ID numérico do produto que deseja editar")
    @is_admin()
    async def editar_produto(self, interaction: discord.Interaction, product_id: int):
        try:
            res = supabase.table("products").select("*").eq("id", product_id).execute()
            if not res.data:
                await interaction.response.send_message(f"❌ Nenhum produto encontrado com o ID `{product_id}`.", ephemeral=True)
                return

            prod = res.data[0]
            modal = EditarProdutoModal(
                product_id=prod["id"],
                current_name=prod["name"],
                current_desc=prod["description"],
                current_delivery=prod.get("delivery_content", ""),
                current_price=prod["price"],
                current_img=prod.get("image_url", "")
            )
            await interaction.response.send_modal(modal)
        except Exception as e:
            await interaction.response.send_message(f"❌ Erro ao carregar produto para edição: {e}", ephemeral=True)

    @app_commands.command(name="set_banner", description="Altera o banner principal da loja (Admin)")
    @app_commands.describe(url="Link direto da imagem do banner")
    @is_admin()
    async def set_banner(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            supabase.table("store_config").update({"banner_url": url}).eq("id", 1).execute()
            await interaction.followup.send(f"✅ Banner atualizado com sucesso!\n🖼️ **URL:** {url}", ephemeral=True)
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
            await interaction.followup.send(f"✅ Cor do tema alterada para **{cor}**!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao atualizar cor: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Store(bot))
