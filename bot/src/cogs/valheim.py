"""Valheim server control commands.

Slash commands:
    /valheim status -- describe current VM + game state, including the
                       PlayFab join code and server password
    /valheim start  -- start the VM (idempotent)
    /valheim stop   -- stop the VM (idempotent)

Each handler defers, calls into src.services.compute / server_query,
then sends a response so the channel sees the result.
"""

import discord
from discord import app_commands
from discord.ext import commands

from src.config.logging import get_logger
from src.config.secrets import get_secrets
from src.config.settings import get_settings
from src.services import compute, server_query
from src.services.compute import InstanceState
from src.services.server_query import LiveStatus

logger = get_logger(__name__)


_STATUS_COLORS: dict[str, int] = {
    "RUNNING": 0x2ECC71,  # green
    "PROVISIONING": 0xF1C40F,  # amber
    "STAGING": 0xF1C40F,
    "STOPPING": 0xE67E22,  # orange
    "TERMINATED": 0x95A5A6,  # grey
}


def build_status_embed(
    state: InstanceState,
    live: LiveStatus | None,
    password: str | None,
) -> discord.Embed:
    """Render an InstanceState + optional LiveStatus + password into a Discord embed.

    Pure function -- no I/O -- so the rendering branches are unit-
    testable without mocking interactions.
    """
    color = _STATUS_COLORS.get(state.status, 0x3498DB)
    embed = discord.Embed(
        title=f"Valheim — {state.status}",
        color=color,
    )
    embed.add_field(name="Instance", value=f"`{state.name}` ({state.machine_type})", inline=False)
    embed.add_field(name="Zone", value=state.zone, inline=True)
    if state.public_ip:
        embed.add_field(name="Address", value=f"`{state.public_ip}:2456`", inline=True)

    if live is not None and live.server_running:
        if live.join_code:
            embed.add_field(name="Join code", value=f"`{live.join_code}`", inline=True)
        embed.add_field(name="Players", value=str(live.player_count), inline=True)
        if password:
            embed.add_field(name="Password", value=f"`{password}`", inline=True)
    elif state.status == "RUNNING":
        embed.add_field(
            name="Game server",
            value="VM is up but the game server isn't answering yet (Valheim takes "
            "~60-90s to boot after the VM does). Try `/valheim status` again shortly.",
            inline=False,
        )

    return embed


class ValheimCog(commands.GroupCog, name="valheim"):
    """The /valheim command group."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._settings = get_settings()
        super().__init__()

    def _target(self) -> tuple[str, str, str]:
        s = self._settings
        return s.gcp_project_id, s.valheim_zone, s.valheim_instance_name

    @app_commands.command(name="status", description="Show Valheim server status")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        logger.info("Valheim status requested", user=str(interaction.user))
        project, zone, instance = self._target()
        state = await compute.describe_instance(project, zone, instance)

        live: LiveStatus | None = None
        if state.status == "RUNNING" and state.public_ip:
            live = await server_query.fetch_status(
                state.public_ip,
                port=self._settings.valheim_status_http_port,
            )

        # Password is in GSM; fetch every status call so a rotation is
        # picked up without restarting the bot. Cheap (cached on the
        # SecretManager after first call).
        password: str | None = None
        if state.status == "RUNNING":
            password = get_secrets(self._settings.discord_bot_name).valheim_password

        await interaction.followup.send(embed=build_status_embed(state, live, password))

    @app_commands.command(name="start", description="Start the Valheim server")
    async def start(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        logger.info("Valheim start requested", user=str(interaction.user))
        project, zone, instance = self._target()
        state = await compute.describe_instance(project, zone, instance)
        if state.status == "RUNNING":
            ip = state.public_ip or "address pending"
            await interaction.followup.send(content=f"Server already running at `{ip}:2456`.")
            return
        await compute.start_instance(project, zone, instance)
        await interaction.followup.send(
            content="Starting the Valheim server. Run `/valheim status` in ~90s."
        )

    @app_commands.command(name="stop", description="Stop the Valheim server")
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        logger.info("Valheim stop requested", user=str(interaction.user))
        project, zone, instance = self._target()
        state = await compute.describe_instance(project, zone, instance)
        if state.status == "TERMINATED":
            await interaction.followup.send(content="Server is already stopped.")
            return
        await compute.stop_instance(project, zone, instance)
        await interaction.followup.send(content="Stopping the Valheim server.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ValheimCog(bot))
