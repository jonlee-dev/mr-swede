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

        # Mr. Swede is multi-feature now: Valheim server controls,
        # music playback, and a foundation for whatever cog ships next.
        # Keep this embed in sync with new top-level command groups.
        embed = discord.Embed(
            title="Mr. Swede",
            description=(
                "A do-it-all Discord bot for our server. Right now: a "
                "Valheim on-demand game server and a Lavalink-backed "
                "music player, with more on the way."
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Valheim",
            value=(
                "`/valheim start` `/valheim stop` `/valheim status`\n"
                "On-demand GCE VM. Auto-stops after the server is empty for ~60-90 min."
            ),
            inline=False,
        )
        embed.add_field(
            name="Music",
            value=(
                "`/music play` `/music skip` `/music pause` `/music resume` `/music stop`\n"
                "`/music queue` `/music nowplaying` `/music volume` `/music shuffle` `/music loop`\n"
                "Lavalink + YouTube + Spotify (track / playlist / album URLs). "
                "Invoke from the music command channel; joins whatever voice "
                "channel you're in."
            ),
            inline=False,
        )
        embed.add_field(
            name="Diagnostics",
            value="`/ping` `/info`",
            inline=False,
        )
        embed.set_footer(text=f"v{__version__} | {len(self.bot.guilds)} server(s)")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DiagnosticsCog(bot))
