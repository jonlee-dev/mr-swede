"""Discord bot setup and configuration."""

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from src.config.logging import get_logger
from src.config.secrets import get_secrets
from src.config.settings import get_settings

logger = get_logger(__name__)


# Cogs loaded at startup. Order matters only insofar as later cogs can
# depend on earlier ones (none currently do).
COG_MODULES = (
    "src.cogs.diagnostics",
    "src.cogs.valheim",
)


class MrSwede(commands.Bot):
    """Mr. Swede Discord bot.

    Slash-command-only. We don't use legacy `!`/`$` prefix commands, so
    `message_content` and `voice_states` intents stay off.
    """

    def __init__(self) -> None:
        settings = get_settings()
        secrets = get_secrets(discord_bot_name=settings.discord_bot_name)

        # Default intents are sufficient for slash commands. Adding privileged
        # intents (members, presences, message_content) requires opting in
        # via the Discord developer portal -- we don't need them.
        intents = discord.Intents.default()

        super().__init__(
            command_prefix="!",  # Required by discord.py but unused; we are slash-only
            intents=intents,
            help_command=None,
        )

        self.settings = settings
        self.secrets = secrets

    async def on_ready(self) -> None:
        if self.user:
            logger.info(
                "Bot ready",
                user=str(self.user),
                user_id=self.user.id,
                guilds=len(self.guilds),
                bot_name=self.settings.discord_bot_name,
            )
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Valheim",
            )
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        logger.info("Joined guild", guild=guild.name, guild_id=guild.id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        logger.info("Left guild", guild=guild.name, guild_id=guild.id)

    async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:
        logger.exception("Error in event", event_method=event_method)

    async def setup_hook(self) -> None:
        self.tree.on_error = self._on_app_command_error  # type: ignore[method-assign]
        await self._load_cogs()
        await self._sync_commands()

    async def _on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        original = error.original if isinstance(error, app_commands.CommandInvokeError) else error

        # Interaction expired -- nothing we can send to the user.
        if isinstance(original, discord.NotFound) and original.code == 10062:
            logger.warning(
                "Interaction expired",
                command=interaction.command.name if interaction.command else "unknown",
                user=str(interaction.user),
            )
            return

        logger.error(
            "App command error",
            command=interaction.command.name if interaction.command else "unknown",
            user=str(interaction.user),
            error=str(original),
        )

        try:
            message = "An error occurred while processing your command."
            if interaction.response.is_done():
                await interaction.followup.send(f"{message}", ephemeral=True)
            else:
                await interaction.response.send_message(f"{message}", ephemeral=True)
        except discord.NotFound:
            return  # Already expired

    async def _load_cogs(self) -> None:
        for cog in COG_MODULES:
            try:
                await self.load_extension(cog)
                logger.info("Loaded cog", cog=cog)
            except Exception as e:
                logger.error("Failed to load cog", cog=cog, error=str(e))

    async def _sync_commands(self) -> None:
        if self.settings.discord_guild_id:
            # Guild-scoped sync is instant; global sync can take up to an hour.
            guild = discord.Object(id=int(self.settings.discord_guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synced commands to guild", guild_id=self.settings.discord_guild_id)
        else:
            await self.tree.sync()
            logger.info("Synced commands globally")


def create_bot() -> MrSwede:
    """Create and configure the bot instance."""
    return MrSwede()


def get_bot_token() -> str:
    """Return the Discord bot token from GSM, falling back to env.

    Raises:
        ValueError: If neither source has a token.
    """
    settings = get_settings()
    secrets = get_secrets(discord_bot_name=settings.discord_bot_name)

    if secrets.discord and secrets.discord.token:
        return secrets.discord.token

    if settings.discord_token:
        return settings.discord_token.get_secret_value()

    raise ValueError(
        f"No Discord token found for bot '{settings.discord_bot_name}'. "
        "Ensure secrets are configured in GSM or set DISCORD_TOKEN env var."
    )
