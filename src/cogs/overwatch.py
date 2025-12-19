"""Overwatch stats tracking commands."""

from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from src.config.logging import get_logger
from src.database import Account, CompetitiveStats, StatsHistory, get_firestore_client
from src.services import OverfastClient

logger = get_logger(__name__)


class OverwatchCog(commands.Cog, name="Overwatch"):
    """Overwatch stats tracking commands."""
    
    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog.
        
        Args:
            bot: Discord bot instance
        """
        self.bot = bot
        self.overfast = OverfastClient()
        self.db = get_firestore_client()
    
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Handle bot ready event."""
        logger.info("OverwatchCog ready")
    
    ow_group = app_commands.Group(name="ow", description="Overwatch commands")
    
    @ow_group.command(name="stats", description="Get Overwatch stats for a player")
    @app_commands.describe(battletag="Player BattleTag (e.g., Player#1234)")
    async def stats(self, interaction: discord.Interaction, battletag: str) -> None:
        """Get stats for a player.
        
        Args:
            interaction: Discord interaction
            battletag: Player's BattleTag
        """
        await interaction.response.defer()
        
        try:
            stats = await self.overfast.get_competitive_stats(battletag)
            
            embed = self._create_stats_embed(battletag, stats)
            await interaction.followup.send(embed=embed)
            
            logger.info("Stats fetched", battletag=battletag, user=str(interaction.user))
            
        except Exception as e:
            logger.error("Failed to fetch stats", battletag=battletag, error=str(e))
            embed = discord.Embed(
                title="❌ Error",
                description=f"Could not fetch stats for `{battletag}`.\n\n"
                           "Make sure the BattleTag is correct and the profile is public.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
    
    @ow_group.command(name="track", description="Start tracking an Overwatch account")
    @app_commands.describe(
        battletag="Player BattleTag (e.g., Player#1234)",
        name="Display name for this account",
        main="Set as your main account",
    )
    async def track(
        self, 
        interaction: discord.Interaction, 
        battletag: str,
        name: str | None = None,
        main: bool = False,
    ) -> None:
        """Track an Overwatch account.
        
        Args:
            interaction: Discord interaction
            battletag: Player's BattleTag
            name: Optional display name
            main: Whether this is the main account
        """
        await interaction.response.defer()
        
        try:
            # Check if already tracked
            existing = await self.db.get_account_by_battle_tag(battletag)
            if existing:
                embed = discord.Embed(
                    title="⚠️ Already Tracked",
                    description=f"`{battletag}` is already being tracked.",
                    color=discord.Color.orange(),
                )
                await interaction.followup.send(embed=embed)
                return
            
            # Verify the account exists
            stats = await self.overfast.get_competitive_stats(battletag)
            
            # If setting as main, unset other main accounts
            if main:
                user_accounts = await self.db.get_accounts_by_discord_user(
                    str(interaction.user.id)
                )
                for acc in user_accounts:
                    if acc.is_main:
                        await self.db.update_account(acc.id, {"is_main": False})
            
            # Create the account
            account = Account(
                battle_tag=battletag,
                discord_user_id=str(interaction.user.id),
                display_name=name or battletag.split("#")[0],
                is_main=main,
                current_stats=stats,
                last_updated=datetime.utcnow(),
            )
            
            account_id = await self.db.create_account(account)
            
            # Save initial stats history
            history = StatsHistory(
                account_id=account_id,
                battle_tag=battletag,
                stats=stats,
            )
            await self.db.add_stats_history(history)
            
            embed = discord.Embed(
                title="✅ Account Tracked",
                description=f"Now tracking `{battletag}`",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="Current Ranks",
                value=(
                    f"🛡️ Tank: {stats.tank.display}\n"
                    f"⚔️ Damage: {stats.damage.display}\n"
                    f"💚 Support: {stats.support.display}"
                ),
            )
            
            await interaction.followup.send(embed=embed)
            logger.info("Account tracked", battletag=battletag, user=str(interaction.user))
            
        except Exception as e:
            logger.error("Failed to track account", battletag=battletag, error=str(e))
            embed = discord.Embed(
                title="❌ Error",
                description=f"Could not track `{battletag}`.\n\n"
                           "Make sure the BattleTag is correct and the profile is public.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
    
    @ow_group.command(name="untrack", description="Stop tracking an Overwatch account")
    @app_commands.describe(battletag="Player BattleTag to stop tracking")
    async def untrack(self, interaction: discord.Interaction, battletag: str) -> None:
        """Stop tracking an account.
        
        Args:
            interaction: Discord interaction
            battletag: BattleTag to untrack
        """
        await interaction.response.defer()
        
        try:
            account = await self.db.get_account_by_battle_tag(battletag)
            
            if not account:
                embed = discord.Embed(
                    title="⚠️ Not Found",
                    description=f"`{battletag}` is not being tracked.",
                    color=discord.Color.orange(),
                )
                await interaction.followup.send(embed=embed)
                return
            
            # Verify ownership
            if account.discord_user_id != str(interaction.user.id):
                embed = discord.Embed(
                    title="❌ Permission Denied",
                    description="You can only untrack your own accounts.",
                    color=discord.Color.red(),
                )
                await interaction.followup.send(embed=embed)
                return
            
            await self.db.delete_account(account.id)
            
            embed = discord.Embed(
                title="✅ Account Untracked",
                description=f"Stopped tracking `{battletag}`",
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed)
            logger.info("Account untracked", battletag=battletag, user=str(interaction.user))
            
        except Exception as e:
            logger.error("Failed to untrack account", battletag=battletag, error=str(e))
            embed = discord.Embed(
                title="❌ Error",
                description="An error occurred while untracking the account.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
    
    @ow_group.command(name="list", description="List all tracked accounts")
    async def list_accounts(self, interaction: discord.Interaction) -> None:
        """List tracked accounts.
        
        Args:
            interaction: Discord interaction
        """
        await interaction.response.defer()
        
        try:
            accounts = await self.db.get_accounts_by_discord_user(str(interaction.user.id))
            
            if not accounts:
                embed = discord.Embed(
                    title="📋 Tracked Accounts",
                    description="You're not tracking any accounts yet.\n"
                               "Use `/ow track <battletag>` to start tracking.",
                    color=discord.Color.blue(),
                )
                await interaction.followup.send(embed=embed)
                return
            
            embed = discord.Embed(
                title="📋 Tracked Accounts",
                color=discord.Color.blue(),
            )
            
            for account in accounts:
                main_badge = "⭐ " if account.is_main else ""
                stats = account.current_stats
                
                value = (
                    f"🛡️ {stats.tank.display} | "
                    f"⚔️ {stats.damage.display} | "
                    f"💚 {stats.support.display}"
                )
                
                if account.last_updated:
                    value += f"\n*Updated: {account.last_updated.strftime('%Y-%m-%d %H:%M')}*"
                
                embed.add_field(
                    name=f"{main_badge}{account.display_name} ({account.battle_tag})",
                    value=value,
                    inline=False,
                )
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error("Failed to list accounts", error=str(e))
            embed = discord.Embed(
                title="❌ Error",
                description="An error occurred while fetching accounts.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
    
    @ow_group.command(name="refresh", description="Refresh stats for all tracked accounts")
    async def refresh(self, interaction: discord.Interaction) -> None:
        """Refresh all tracked accounts.
        
        Args:
            interaction: Discord interaction
        """
        await interaction.response.defer()
        
        try:
            accounts = await self.db.get_accounts_by_discord_user(str(interaction.user.id))
            
            if not accounts:
                embed = discord.Embed(
                    title="⚠️ No Accounts",
                    description="You're not tracking any accounts.",
                    color=discord.Color.orange(),
                )
                await interaction.followup.send(embed=embed)
                return
            
            updated = 0
            errors = 0
            
            for account in accounts:
                try:
                    stats = await self.overfast.get_competitive_stats(account.battle_tag)
                    await self.db.update_account_stats(account.id, stats)
                    
                    # Record history
                    history = StatsHistory(
                        account_id=account.id,
                        battle_tag=account.battle_tag,
                        stats=stats,
                    )
                    await self.db.add_stats_history(history)
                    
                    updated += 1
                except Exception as e:
                    logger.error(
                        "Failed to refresh account", 
                        battletag=account.battle_tag, 
                        error=str(e)
                    )
                    errors += 1
            
            embed = discord.Embed(
                title="🔄 Stats Refreshed",
                description=f"Updated {updated} account(s)",
                color=discord.Color.green() if errors == 0 else discord.Color.orange(),
            )
            
            if errors > 0:
                embed.add_field(
                    name="⚠️ Errors",
                    value=f"Failed to update {errors} account(s)",
                )
            
            await interaction.followup.send(embed=embed)
            logger.info("Stats refreshed", updated=updated, errors=errors)
            
        except Exception as e:
            logger.error("Failed to refresh stats", error=str(e))
            embed = discord.Embed(
                title="❌ Error",
                description="An error occurred while refreshing stats.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
    
    @ow_group.command(name="leaderboard", description="Show rank leaderboard for tracked accounts")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        """Show leaderboard of all tracked accounts.
        
        Args:
            interaction: Discord interaction
        """
        await interaction.response.defer()
        
        try:
            # Get all accounts in the guild
            all_accounts = await self.db.get_all_accounts()
            
            if not all_accounts:
                embed = discord.Embed(
                    title="🏆 Leaderboard",
                    description="No accounts are being tracked yet.",
                    color=discord.Color.blue(),
                )
                await interaction.followup.send(embed=embed)
                return
            
            # Sort by highest rank
            rank_order = [
                "Bronze", "Silver", "Gold", "Platinum",
                "Diamond", "Master", "Grandmaster", "Champion"
            ]
            
            def rank_sort_key(account: Account) -> tuple[int, int]:
                highest = account.current_stats.get_highest_rank()
                if not highest.division:
                    return (-1, 5)
                div_index = rank_order.index(highest.division) if highest.division in rank_order else -1
                return (div_index, -highest.tier)
            
            sorted_accounts = sorted(all_accounts, key=rank_sort_key, reverse=True)[:10]
            
            embed = discord.Embed(
                title="🏆 Overwatch Leaderboard",
                color=discord.Color.gold(),
            )
            
            leaderboard_text = ""
            for i, account in enumerate(sorted_accounts, 1):
                highest = account.current_stats.get_highest_rank()
                medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"#{i}"
                leaderboard_text += f"{medal} **{account.display_name}** - {highest.display}\n"
            
            embed.description = leaderboard_text or "No ranked accounts found."
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error("Failed to generate leaderboard", error=str(e))
            embed = discord.Embed(
                title="❌ Error",
                description="An error occurred while generating the leaderboard.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
    
    def _create_stats_embed(self, battletag: str, stats: CompetitiveStats) -> discord.Embed:
        """Create an embed for player stats.
        
        Args:
            battletag: Player's BattleTag
            stats: Player's competitive stats
            
        Returns:
            Discord embed
        """
        embed = discord.Embed(
            title=f"📊 Stats for {battletag}",
            color=discord.Color.orange(),
        )
        
        embed.add_field(
            name="🛡️ Tank",
            value=stats.tank.display,
            inline=True,
        )
        embed.add_field(
            name="⚔️ Damage",
            value=stats.damage.display,
            inline=True,
        )
        embed.add_field(
            name="💚 Support",
            value=stats.support.display,
            inline=True,
        )
        
        highest = stats.get_highest_rank()
        if highest.division:
            embed.add_field(
                name="🏆 Highest Rank",
                value=highest.display,
                inline=False,
            )
        
        embed.set_footer(text="Data from Overfast API")
        
        return embed


async def setup(bot: commands.Bot) -> None:
    """Load the cog.
    
    Args:
        bot: Discord bot instance
    """
    await bot.add_cog(OverwatchCog(bot))

