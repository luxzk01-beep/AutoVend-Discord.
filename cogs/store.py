class Store(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="loja", description="Envia o catálogo fixo da loja no canal para todos verem (Admin)")
    @is_admin()
    async def loja(self, interaction: discord.Interaction):
        # Removemos o ephemeral=True para a mensagem ficar visível para todos no canal
        await interaction.response.defer(thinking=True)
        try:
            res = supabase.table("products").select("*").execute()
            products = res.data

            if not products:
                await interaction.followup.send("❌ Não há produtos cadastrados no momento. Use `/adicionar_produto` para cadastrar.")
                return

            view = CatalogView(products)
            # Envia publicamente no canal
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
