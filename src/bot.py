"""Discord bot setup and configuration."""

import discord
from discord.ext import commands

from src.config.logging import get_logger, setup_logging
from src.config.settings import get_settings

logger = get_logger(__name__)


class MrSwede(commands.Bot):
    """Mr. Swede Discord bot."""
    
    def __init__(self) -> None:
        """Initialize the bot."""
        settings = get_settings()
        
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
    
    async def setup_hook(self) -> None:
        """Called before the bot starts, used for async setup."""
        # Load cogs
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
        
        # Sync slash commands
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
    
    async def on_ready(self) -> None:
        """Called when the bot is ready."""
        if self.user:
            logger.info(
                "Bot ready",
                user=str(self.user),
                user_id=self.user.id,
                guilds=len(self.guilds),
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


def create_bot() -> MrSwede:
    """Create and configure the bot instance.
    
    Returns:
        Configured MrSwede bot instance
    """
    setup_logging()
    return MrSwede()

