class ProductSelect(discord.ui.Select):
    def __init__(self, products):
        self.products = products
        options = []
        
        for index, product in enumerate(products):
            # Limita os tamanhos permitidos pelo Discord para o Label e Descrição
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
        # Salva o índice selecionado e atualiza a visualização para o produto escolhido
        view: CatalogSelectView = self.view
        view.index = int(self.values[0])
        await interaction.response.edit_message(embed=view.get_embed(), view=view)


class CatalogSelectView(discord.ui.View):
    def __init__(self, products, index=0):
        super().__init__(timeout=None)
        self.products = products
        self.index = index
        # Adiciona o menu suspenso na view
        self.add_item(ProductSelect(products))

    def get_embed(self):
        if not self.products:
            return discord.Embed(title="Loja Vazia", description="Não há produtos cadastrados.", color=discord.Color.red())
        
        product = self.products[self.index]
        embed = discord.Embed(
            title=product['name'],
            description=product['description'],
            color=discord.Color.blue()
        )
        embed.add_field(name="Preço", value=f"**R$ {product['price']:.2f}**", inline=False)
        
        img_url = product.get('image_url')
        if img_url and isinstance(img_url, str) and img_url.startswith("http"):
            try:
                embed.set_image(url=img_url)
            except Exception:
                pass

        embed.set_footer(text=f"Produto {self.index + 1} de {len(self.products)}")
        return embed

    @discord.ui.button(label="Comprar 🛒", style=discord.ButtonStyle.success, row=1)
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.products:
            await interaction.response.send_message("❌ Nenhum produto disponível para compra.", ephemeral=True)
            return

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
