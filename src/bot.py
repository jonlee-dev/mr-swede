"""Discord bot setup and configuration."""

import discord
from discord import app_commands
from discord.ext import commands

from src.config.logging import get_logger, setup_logging
from src.config.secrets import get_secrets
from src.config.settings import get_settings

logger = get_logger(__name__)


class MrSwede(commands.Bot):
    """Mr. Swede Discord bot."""
    
    def __init__(self) -> None:
        """Initialize the bot."""
        settings = get_settings()
        secrets = get_secrets(discord_bot_name=settings.discord_bot_name)
        
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.guilds = True
        intents.members = True
        
        super().__init__(
            command_prefix="$",  # Legacy prefix commands (optional)
            intents=intents,
            help_command=None,  # We use slash commands instead
        )
        
        self.settings = settings
        self.secrets = secrets
    
    async def on_ready(self) -> None:
        """Called when the bot is ready."""
        if self.user:
            logger.info(
                "Bot ready",
                user=str(self.user),
                user_id=self.user.id,
                guilds=len(self.guilds),
                bot_name=self.settings.discord_bot_name,
            )
        
        # Set presence
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="/help",
            )
        )
    
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Called when the bot joins a guild."""
        logger.info("Joined guild", guild=guild.name, guild_id=guild.id)
    
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Called when the bot is removed from a guild."""
        logger.info("Left guild", guild=guild.name, guild_id=guild.id)
    
    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        """Handle errors in event handlers."""
        logger.exception("Error in event", event=event_method)
    
    async def setup_hook(self) -> None:
        """Called before the bot starts, used for async setup."""
        # Set up global error handler for app commands
        self.tree.on_error = self._on_app_command_error
        
        # Load cogs
        await self._load_cogs()
        
        # Sync slash commands
        await self._sync_commands()
    
    async def _on_app_command_error(
        self, 
        interaction: discord.Interaction, 
        error: app_commands.AppCommandError,
    ) -> None:
        """Handle errors in app commands."""
        # Unwrap the error if it's wrapped
        original = error.original if isinstance(error, app_commands.CommandInvokeError) else error
        
        # Handle "Unknown interaction" errors (interaction expired)
        if isinstance(original, discord.NotFound) and original.code == 10062:
            logger.warning(
                "Interaction expired",
                command=interaction.command.name if interaction.command else "unknown",
                user=str(interaction.user),
            )
            return  # Can't respond to expired interaction
        
        # Log other errors
        logger.error(
            "App command error",
            command=interaction.command.name if interaction.command else "unknown",
            user=str(interaction.user),
            error=str(original),
        )
        
        # Try to respond to the user
        try:
            message = "An error occurred while processing your command."
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ {message}", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ {message}", ephemeral=True)
        except discord.NotFound:
            pass  # Interaction already expired
    
    async def _load_cogs(self) -> None:
        """Load all cog modules."""
        cog_modules = [
            "src.cogs.general",
            "src.cogs.overwatch",
            "src.cogs.music",
        ]
        
        for cog in cog_modules:
            try:
                await self.load_extension(cog)
                logger.info("Loaded cog", cog=cog)
            except Exception as e:
                logger.error("Failed to load cog", cog=cog, error=str(e))
    
    async def _sync_commands(self) -> None:
        """Sync slash commands with Discord."""
        if self.settings.discord_guild_id:
            # Sync to specific guild for faster updates during development
            guild = discord.Object(id=int(self.settings.discord_guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synced commands to guild", guild_id=self.settings.discord_guild_id)
        else:
            # Sync globally (takes up to an hour to propagate)
            await self.tree.sync()
            logger.info("Synced commands globally")


def create_bot() -> MrSwede:
    """Create and configure the bot instance.
    
    Returns:
        Configured MrSwede bot instance
    """
    setup_logging()
    return MrSwede()


def get_bot_token() -> str:
    """Get the Discord bot token from secrets.
    
    Returns:
        Discord bot token
        
    Raises:
        ValueError: If no token is available
    """
    settings = get_settings()
    secrets = get_secrets(discord_bot_name=settings.discord_bot_name)
    
    if secrets.discord and secrets.discord.token:
        return secrets.discord.token
    
    # Fallback to environment variable
    if settings.discord_token:
        return settings.discord_token.get_secret_value()
    
    raise ValueError(
        f"No Discord token found for bot '{settings.discord_bot_name}'. "
        "Ensure secrets are configured in GSM or set DISCORD_TOKEN env var."
    )
