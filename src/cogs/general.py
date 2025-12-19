"""General utility commands for the Discord bot."""

import discord
from discord import app_commands
from discord.ext import commands

from src.config.logging import get_logger

logger = get_logger(__name__)


class GeneralCog(commands.Cog, name="General"):
    """General utility commands."""
    
    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog.
        
        Args:
            bot: Discord bot instance
        """
        self.bot = bot
    
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Handle bot ready event."""
        logger.info("GeneralCog ready")
    
    @app_commands.command(name="ping", description="Check bot latency")
    async def ping(self, interaction: discord.Interaction) -> None:
        """Check bot latency.
        
        Args:
            interaction: Discord interaction
        """
        latency_ms = round(self.bot.latency * 1000)
        
        embed = discord.Embed(
            title="🏓 Pong!",
            description=f"Latency: **{latency_ms}ms**",
            color=discord.Color.green() if latency_ms < 200 else discord.Color.orange(),
        )
        
        await interaction.response.send_message(embed=embed)
        logger.info("Ping command", latency_ms=latency_ms, user=str(interaction.user))
    
    @app_commands.command(name="info", description="Get bot information")
    async def info(self, interaction: discord.Interaction) -> None:
        """Display bot information.
        
        Args:
            interaction: Discord interaction
        """
        embed = discord.Embed(
            title="🇸🇪 Mr. Swede",
            description="A Swiss-army-knife Discord bot for Overwatch stats and music.",
            color=discord.Color.blue(),
        )
        
        embed.add_field(
            name="Features",
            value=(
                "• Overwatch stats tracking\n"
                "• Music playback (YouTube/Spotify search)\n"
                "• Multi-account support\n"
            ),
            inline=False,
        )
        
        embed.add_field(
            name="Commands",
            value="Use `/help` to see all available commands",
            inline=False,
        )
        
        embed.set_footer(text=f"Running on {len(self.bot.guilds)} server(s)")
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="help", description="Get help with commands")
    @app_commands.describe(category="Command category to get help for")
    @app_commands.choices(category=[
        app_commands.Choice(name="Overwatch", value="overwatch"),
        app_commands.Choice(name="Music", value="music"),
        app_commands.Choice(name="General", value="general"),
    ])
    async def help_command(
        self, 
        interaction: discord.Interaction,
        category: str | None = None,
    ) -> None:
        """Display help information.
        
        Args:
            interaction: Discord interaction
            category: Optional category filter
        """
        embed = discord.Embed(
            title="📖 Help",
            color=discord.Color.blue(),
        )
        
        if category is None or category == "general":
            embed.add_field(
                name="🔧 General Commands",
                value=(
                    "`/ping` - Check bot latency\n"
                    "`/info` - Bot information\n"
                    "`/help` - This help message\n"
                ),
                inline=False,
            )
        
        if category is None or category == "overwatch":
            embed.add_field(
                name="🎮 Overwatch Commands",
                value=(
                    "`/ow stats <battletag>` - Get player stats\n"
                    "`/ow track <battletag>` - Track an account\n"
                    "`/ow untrack <battletag>` - Stop tracking\n"
                    "`/ow list` - List tracked accounts\n"
                    "`/ow refresh` - Refresh all stats\n"
                ),
                inline=False,
            )
        
        if category is None or category == "music":
            embed.add_field(
                name="🎵 Music Commands",
                value=(
                    "`/play <query>` - Play a song\n"
                    "`/pause` - Pause playback\n"
                    "`/resume` - Resume playback\n"
                    "`/skip` - Skip current song\n"
                    "`/queue` - Show queue\n"
                    "`/stop` - Stop and clear queue\n"
                    "`/leave` - Disconnect from voice\n"
                ),
                inline=False,
            )
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="invite", description="Get bot invite link")
    async def invite(self, interaction: discord.Interaction) -> None:
        """Generate bot invite link.
        
        Args:
            interaction: Discord interaction
        """
        permissions = discord.Permissions(
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            use_external_emojis=True,
            connect=True,
            speak=True,
            use_voice_activation=True,
        )
        
        invite_url = discord.utils.oauth_url(
            self.bot.user.id if self.bot.user else "",
            permissions=permissions,
        )
        
        embed = discord.Embed(
            title="📨 Invite Mr. Swede",
            description=f"[Click here to invite]({invite_url})",
            color=discord.Color.blue(),
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Load the cog.
    
    Args:
        bot: Discord bot instance
    """
    await bot.add_cog(GeneralCog(bot))

