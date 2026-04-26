"""Diagnostics commands: /ping and /info.

Kept deliberately minimal. Discord auto-documents slash commands in its UI,
so we don't need a /help command. /invite is unnecessary for a single-server bot.
"""

import discord
from discord import app_commands
from discord.ext import commands

from src.config.logging import get_logger

logger = get_logger(__name__)


class DiagnosticsCog(commands.Cog, name="Diagnostics"):
    """Bot diagnostics: latency and version info."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="Check bot latency")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        color = discord.Color.green() if latency_ms < 200 else discord.Color.orange()

        embed = discord.Embed(
            title="Pong",
            description=f"Latency: **{latency_ms}ms**",
            color=color,
        )
        await interaction.response.send_message(embed=embed)
        logger.info("Ping command", latency_ms=latency_ms, user=str(interaction.user))

    @app_commands.command(name="info", description="Get bot information")
    async def info(self, interaction: discord.Interaction) -> None:
        from src import __version__

        embed = discord.Embed(
            title="Mr. Swede",
            description="Discord-controlled Valheim server.",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Commands",
            value="`/valheim status` `/valheim start` `/valheim stop`",
            inline=False,
        )
        embed.set_footer(text=f"v{__version__} | {len(self.bot.guilds)} server(s)")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DiagnosticsCog(bot))
