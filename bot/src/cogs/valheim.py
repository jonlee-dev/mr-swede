"""Valheim server control commands.

Slash commands:
    /valheim status -- describe current VM + game state
    /valheim start  -- start the VM
    /valheim stop   -- stop the VM

Phase 2 scaffold: handlers raise NotImplementedError. Phase 3 wires them up
to src.services.compute and src.services.server_query.
"""

import discord
from discord import app_commands
from discord.ext import commands

from src.config.logging import get_logger

logger = get_logger(__name__)


class ValheimCog(commands.GroupCog, name="valheim"):
    """The /valheim command group."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    @app_commands.command(name="status", description="Show Valheim server status")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        logger.info("Valheim status requested", user=str(interaction.user))
        # TODO(phase-3): call src.services.compute.describe_instance()
        # then src.services.server_query.query() if VM is RUNNING.
        await interaction.followup.send(
            "Not implemented yet. Phase 3 will wire this to GCE + A2S query.",
            ephemeral=True,
        )

    @app_commands.command(name="start", description="Start the Valheim server")
    async def start(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        logger.info("Valheim start requested", user=str(interaction.user))
        # TODO(phase-3): call src.services.compute.start_instance()
        await interaction.followup.send(
            "Not implemented yet. Phase 3 will wire this to GCE.",
            ephemeral=True,
        )

    @app_commands.command(name="stop", description="Stop the Valheim server")
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        logger.info("Valheim stop requested", user=str(interaction.user))
        # TODO(phase-3): call src.services.compute.stop_instance()
        await interaction.followup.send(
            "Not implemented yet. Phase 3 will wire this to GCE.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ValheimCog(bot))
