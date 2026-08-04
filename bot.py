import os
import asyncio
import discord
from discord.ext import commands
from supabase import create_client, Client
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

class SalesBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    async def setup_hook(self):
        await self.load_extension("cogs.store")
        await self.tree.sync()
        print("Comandos Slash e Cogs carregados com sucesso!")

bot = SalesBot()

# --- SERVER WEB PARA O PLANO FREE DO RENDER ---
async def handle_ping(request):
    return web.Response(text="Bot de Vendas Online!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Servidor Web ativo na porta {port}")

async def main():
    await start_web_server()
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
