"""Music playback commands for Discord voice channels."""

import asyncio
import os
from collections import deque
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from src.config.logging import get_logger
from src.services import YouTubeAudioClient, get_spotify_client, preload_cookies
from src.services.youtube import AudioTrack, COOKIES_TEMP_FILE, _fetch_cookies_from_gsm_async

logger = get_logger(__name__)

# Bot owner Discord ID for cookie expiration notifications
# Set via DISCORD_OWNER_ID env var
BOT_OWNER_ID = os.environ.get("DISCORD_OWNER_ID", "")


class MusicQueue:
    """Music queue for a guild."""
    
    def __init__(self) -> None:
        """Initialize the queue."""
        self.tracks: deque[AudioTrack] = deque()
        self.current: AudioTrack | None = None
        self.loop_mode: str = "off"  # off, single, queue
        self.volume: float = 0.5
    
    def add(self, track: AudioTrack) -> None:
        """Add a track to the queue."""
        self.tracks.append(track)
    
    def add_next(self, track: AudioTrack) -> None:
        """Add a track to play next."""
        self.tracks.appendleft(track)
    
    def pop(self) -> AudioTrack | None:
        """Get the next track."""
        if self.tracks:
            return self.tracks.popleft()
        return None
    
    def clear(self) -> None:
        """Clear the queue."""
        self.tracks.clear()
        self.current = None
    
    def shuffle(self) -> None:
        """Shuffle the queue."""
        import random
        tracks_list = list(self.tracks)
        random.shuffle(tracks_list)
        self.tracks = deque(tracks_list)
    
    def __len__(self) -> int:
        return len(self.tracks)


class MusicCog(commands.Cog, name="Music"):
    """Music playback commands."""
    
    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog.
        
        Args:
            bot: Discord bot instance
        """
        self.bot = bot
        self.youtube = YouTubeAudioClient()
        self.spotify = get_spotify_client()  # May be None if not configured
        self.queues: dict[int, MusicQueue] = {}  # guild_id -> queue
        self._cookie_expiry_notified = False  # Track if we've already notified
        
        if not self.spotify:
            logger.warning("Spotify client not available - Spotify URL support disabled")
    
    async def _notify_cookie_expiry(self) -> None:
        """Send a one-time notification to the bot owner about cookie expiration."""
        if self._cookie_expiry_notified:
            return  # Already notified, don't spam
        
        self._cookie_expiry_notified = True
        logger.warning("YouTube cookies have expired - notifying owner")
        
        if not BOT_OWNER_ID:
            logger.warning(
                "DISCORD_OWNER_ID not set - cannot send cookie expiry notification. "
                "Set this env var to receive DM notifications."
            )
            return
        
        try:
            owner_id = int(BOT_OWNER_ID)
            owner = await self.bot.fetch_user(owner_id)
            
            if owner:
                embed = discord.Embed(
                    title="⚠️ YouTube Cookies Expired",
                    description=(
                        "The YouTube cookies have expired and music playback is failing.\n\n"
                        "**To fix:**\n"
                        "1. Export fresh cookies from your browser\n"
                        "2. Upload to Secret Manager:\n"
                        "```\n"
                        "gcloud secrets versions add youtube-cookie \\\n"
                        "  --data-file=cookies.txt \\\n"
                        "  --project=mr-swede\n"
                        "```\n"
                        "3. The bot will automatically pick up the new cookies."
                    ),
                    color=discord.Color.orange(),
                )
                embed.set_footer(text="This notification will only be sent once per restart.")
                
                await owner.send(embed=embed)
                logger.info("Sent cookie expiry notification to owner", owner_id=owner_id)
        except ValueError:
            logger.error("Invalid DISCORD_OWNER_ID - must be an integer")
        except discord.Forbidden:
            logger.warning("Cannot DM owner - they may have DMs disabled")
        except Exception as e:
            logger.error("Failed to send cookie expiry notification", error=str(e))
    
    def _is_cookie_expiry_error(self, error: str) -> bool:
        """Check if an error indicates YouTube cookie expiration."""
        cookie_error_patterns = [
            "Sign in to confirm you're not a bot",
            "cookies",
            "login required",
            "private video",
        ]
        error_lower = error.lower()
        return any(pattern.lower() in error_lower for pattern in cookie_error_patterns)
    
    def get_queue(self, guild_id: int) -> MusicQueue:
        """Get or create a queue for a guild."""
        if guild_id not in self.queues:
            self.queues[guild_id] = MusicQueue()
        return self.queues[guild_id]
    
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Handle bot ready event."""
        # Pre-load YouTube cookies to avoid blocking during playback
        await preload_cookies()
        logger.info("MusicCog ready", spotify_enabled=self.spotify is not None)
    
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Handle voice state changes (auto-disconnect when alone)."""
        if member.bot:
            return
        
        # Check if bot is in a voice channel
        voice_client = member.guild.voice_client
        if not voice_client or not voice_client.channel:
            return
        
        # If everyone left the channel, disconnect after a delay
        if len(voice_client.channel.members) == 1:  # Only bot remains
            await asyncio.sleep(60)  # Wait 1 minute
            
            # Re-check if still alone
            if voice_client.is_connected() and len(voice_client.channel.members) == 1:
                queue = self.get_queue(member.guild.id)
                queue.clear()
                await voice_client.disconnect()
                logger.info("Auto-disconnected due to inactivity", guild=member.guild.name)
    
    @app_commands.command(name="play", description="Play a song or add to queue")
    @app_commands.describe(query="Song name, YouTube URL, or Spotify URL")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        """Play a song.
        
        Args:
            interaction: Discord interaction
            query: Search query or URL
        """
        # Check if user is in a voice channel
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ You must be in a voice channel to use this command.",
                ephemeral=True,
            )
            return
        
        await interaction.response.defer()
        
        voice_channel = interaction.user.voice.channel
        guild = interaction.guild
        
        if not guild:
            return
        
        try:
            # Connect to voice if not already
            voice_client = guild.voice_client
            if not voice_client:
                voice_client = await voice_channel.connect()
            elif voice_client.channel != voice_channel:
                await voice_client.move_to(voice_channel)
            
            # Handle Spotify URLs (only if Spotify client is available)
            if self.spotify and ("spotify.com" in query or query.startswith("spotify:")):
                parsed = self.spotify.parse_spotify_url(query)
                if parsed:
                    item_type, item_id = parsed
                    if item_type == "track":
                        search_query = await self.spotify.get_search_query_for_youtube(item_id)
                        if search_query:
                            query = search_query
                    elif item_type == "playlist":
                        await self._handle_spotify_playlist(interaction, item_id, voice_client)
                        return
            
            # Get audio track
            track = await self.youtube.get_audio_track(query)
            
            if not track:
                embed = discord.Embed(
                    title="❌ Not Found",
                    description="Could not find a playable track.",
                    color=discord.Color.red(),
                )
                await interaction.followup.send(embed=embed)
                return
            
            queue = self.get_queue(guild.id)
            
            # If something is playing, add to queue
            if voice_client.is_playing() or voice_client.is_paused():
                queue.add(track)
                embed = discord.Embed(
                    title="📋 Added to Queue",
                    description=f"**{track.title}**",
                    color=discord.Color.blue(),
                )
                embed.add_field(name="Duration", value=track.duration_str, inline=True)
                embed.add_field(name="Position", value=f"#{len(queue)}", inline=True)
                
                if track.thumbnail:
                    embed.set_thumbnail(url=track.thumbnail)
                
                await interaction.followup.send(embed=embed)
            else:
                # Play immediately
                await self._play_track(voice_client, track, queue)
                
                embed = discord.Embed(
                    title="🎵 Now Playing",
                    description=f"**{track.title}**",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Duration", value=track.duration_str, inline=True)
                embed.add_field(name="Requested by", value=interaction.user.mention, inline=True)
                
                if track.thumbnail:
                    embed.set_thumbnail(url=track.thumbnail)
                
                await interaction.followup.send(embed=embed)
            
            logger.info("Playing track", title=track.title, user=str(interaction.user))
            
        except Exception as e:
            error_str = str(e)
            logger.error("Failed to play", query=query, error=error_str)
            
            # Check if this is a cookie expiration error
            if self._is_cookie_expiry_error(error_str):
                embed = discord.Embed(
                    title="❌ YouTube Authentication Required",
                    description=(
                        "YouTube is blocking the request. This usually means cookies have expired.\n\n"
                        "The bot owner has been notified."
                    ),
                    color=discord.Color.red(),
                )
                # Notify owner (only once)
                asyncio.create_task(self._notify_cookie_expiry())
            else:
                embed = discord.Embed(
                    title="❌ Error",
                    description="An error occurred while trying to play.",
                    color=discord.Color.red(),
                )
            
            await interaction.followup.send(embed=embed)
    
    async def _play_track(
        self, 
        voice_client: discord.VoiceClient, 
        track: AudioTrack,
        queue: MusicQueue,
    ) -> None:
        """Play a track and handle queue progression."""
        queue.current = track
        
        ffmpeg_opts = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn",
        }
        
        source = discord.FFmpegPCMAudio(track.url, **ffmpeg_opts)
        source = discord.PCMVolumeTransformer(source, volume=queue.volume)
        
        def after_playing(error: Exception | None) -> None:
            if error:
                logger.error("Playback error", error=str(error))
            
            # Schedule next track
            asyncio.run_coroutine_threadsafe(
                self._play_next(voice_client, queue),
                self.bot.loop,
            )
        
        voice_client.play(source, after=after_playing)
    
    async def _play_next(
        self, 
        voice_client: discord.VoiceClient, 
        queue: MusicQueue,
    ) -> None:
        """Play the next track in the queue."""
        if not voice_client.is_connected():
            return
        
        # Handle loop modes
        if queue.loop_mode == "single" and queue.current:
            # Re-fetch the track (URL might have expired)
            track = await self.youtube.get_audio_track(queue.current.webpage_url)
            if track:
                await self._play_track(voice_client, track, queue)
                return
        elif queue.loop_mode == "queue" and queue.current:
            queue.add(queue.current)
        
        next_track = queue.pop()
        if next_track:
            # Re-fetch to ensure fresh URL
            track = await self.youtube.get_audio_track(next_track.webpage_url)
            if track:
                await self._play_track(voice_client, track, queue)
        else:
            queue.current = None
    
    async def _handle_spotify_playlist(
        self,
        interaction: discord.Interaction,
        playlist_id: str,
        voice_client: discord.VoiceClient,
    ) -> None:
        """Handle Spotify playlist."""
        if not self.spotify:
            embed = discord.Embed(
                title="❌ Spotify Not Configured",
                description="Spotify support is not available.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
            return
        
        tracks = await self.spotify.get_playlist_tracks(playlist_id, limit=25)
        
        if not tracks:
            embed = discord.Embed(
                title="❌ Empty Playlist",
                description="Could not find tracks in this playlist.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
            return
        
        guild = interaction.guild
        if not guild:
            return
            
        queue = self.get_queue(guild.id)
        added = 0
        
        for track in tracks:
            search_query = f"{' '.join(track['artists'][:2])} {track['name']}"
            audio_track = await self.youtube.get_audio_track(search_query)
            if audio_track:
                queue.add(audio_track)
                added += 1
        
        # Start playing if not already
        if not voice_client.is_playing() and not voice_client.is_paused():
            next_track = queue.pop()
            if next_track:
                await self._play_track(voice_client, next_track, queue)
        
        embed = discord.Embed(
            title="📋 Playlist Added",
            description=f"Added **{added}** tracks to the queue.",
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="pause", description="Pause the current track")
    async def pause(self, interaction: discord.Interaction) -> None:
        """Pause playback."""
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message("❌ Not playing anything.", ephemeral=True)
            return
        
        voice_client = interaction.guild.voice_client
        if voice_client.is_playing():
            voice_client.pause()
            await interaction.response.send_message("⏸️ Paused")
        else:
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
    
    @app_commands.command(name="resume", description="Resume playback")
    async def resume(self, interaction: discord.Interaction) -> None:
        """Resume playback."""
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message("❌ Not connected to voice.", ephemeral=True)
            return
        
        voice_client = interaction.guild.voice_client
        if voice_client.is_paused():
            voice_client.resume()
            await interaction.response.send_message("▶️ Resumed")
        else:
            await interaction.response.send_message("❌ Not paused.", ephemeral=True)
    
    @app_commands.command(name="skip", description="Skip the current track")
    async def skip(self, interaction: discord.Interaction) -> None:
        """Skip current track."""
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message("❌ Not playing anything.", ephemeral=True)
            return
        
        voice_client = interaction.guild.voice_client
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()  # This triggers after_playing which plays next
            await interaction.response.send_message("⏭️ Skipped")
        else:
            await interaction.response.send_message("❌ Nothing to skip.", ephemeral=True)
    
    @app_commands.command(name="stop", description="Stop playback and clear queue")
    async def stop(self, interaction: discord.Interaction) -> None:
        """Stop playback and clear queue."""
        if not interaction.guild:
            return
            
        queue = self.get_queue(interaction.guild.id)
        queue.clear()
        
        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()
        
        await interaction.response.send_message("⏹️ Stopped and cleared queue")
    
    @app_commands.command(name="queue", description="Show the music queue")
    async def show_queue(self, interaction: discord.Interaction) -> None:
        """Show the queue."""
        if not interaction.guild:
            return
            
        queue = self.get_queue(interaction.guild.id)
        
        embed = discord.Embed(
            title="🎵 Music Queue",
            color=discord.Color.blue(),
        )
        
        if queue.current:
            embed.add_field(
                name="Now Playing",
                value=f"**{queue.current.title}** ({queue.current.duration_str})",
                inline=False,
            )
        
        if queue.tracks:
            queue_text = ""
            for i, track in enumerate(list(queue.tracks)[:10], 1):
                queue_text += f"`{i}.` {track.title} ({track.duration_str})\n"
            
            if len(queue.tracks) > 10:
                queue_text += f"\n*...and {len(queue.tracks) - 10} more*"
            
            embed.add_field(
                name=f"Up Next ({len(queue.tracks)} tracks)",
                value=queue_text,
                inline=False,
            )
        else:
            if not queue.current:
                embed.description = "The queue is empty."
        
        embed.add_field(
            name="Loop Mode",
            value=queue.loop_mode.title(),
            inline=True,
        )
        embed.add_field(
            name="Volume",
            value=f"{int(queue.volume * 100)}%",
            inline=True,
        )
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="leave", description="Disconnect from voice channel")
    async def leave(self, interaction: discord.Interaction) -> None:
        """Disconnect from voice."""
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message("❌ Not connected to voice.", ephemeral=True)
            return
        
        queue = self.get_queue(interaction.guild.id)
        queue.clear()
        
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("👋 Disconnected")
    
    @app_commands.command(name="volume", description="Set playback volume")
    @app_commands.describe(level="Volume level (0-100)")
    async def volume(self, interaction: discord.Interaction, level: int) -> None:
        """Set volume."""
        if not interaction.guild:
            return
            
        if level < 0 or level > 100:
            await interaction.response.send_message(
                "❌ Volume must be between 0 and 100.", 
                ephemeral=True
            )
            return
        
        queue = self.get_queue(interaction.guild.id)
        queue.volume = level / 100
        
        # Update current source volume if playing
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.source:
            voice_client.source.volume = queue.volume
        
        await interaction.response.send_message(f"🔊 Volume set to {level}%")
    
    @app_commands.command(name="loop", description="Set loop mode")
    @app_commands.describe(mode="Loop mode")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Off", value="off"),
        app_commands.Choice(name="Single Track", value="single"),
        app_commands.Choice(name="Queue", value="queue"),
    ])
    async def loop(self, interaction: discord.Interaction, mode: str) -> None:
        """Set loop mode."""
        if not interaction.guild:
            return
            
        queue = self.get_queue(interaction.guild.id)
        queue.loop_mode = mode
        
        mode_emoji = {"off": "➡️", "single": "🔂", "queue": "🔁"}
        await interaction.response.send_message(
            f"{mode_emoji.get(mode, '')} Loop mode: **{mode.title()}**"
        )
    
    @app_commands.command(name="shuffle", description="Shuffle the queue")
    async def shuffle(self, interaction: discord.Interaction) -> None:
        """Shuffle the queue."""
        if not interaction.guild:
            return
            
        queue = self.get_queue(interaction.guild.id)
        
        if len(queue) < 2:
            await interaction.response.send_message(
                "❌ Need at least 2 tracks in queue to shuffle.",
                ephemeral=True,
            )
            return
        
        queue.shuffle()
        await interaction.response.send_message("🔀 Queue shuffled!")
    
    @app_commands.command(name="nowplaying", description="Show current track info")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        """Show current track."""
        if not interaction.guild:
            return
            
        queue = self.get_queue(interaction.guild.id)
        
        if not queue.current:
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
            return
        
        track = queue.current
        
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**{track.title}**",
            color=discord.Color.green(),
        )
        embed.add_field(name="Duration", value=track.duration_str, inline=True)
        embed.add_field(name="Uploader", value=track.uploader, inline=True)
        
        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)
        
        if track.webpage_url:
            embed.add_field(name="Link", value=f"[YouTube]({track.webpage_url})", inline=False)
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="refresh-cookies", description="[Admin] Refresh YouTube cookies from Secret Manager")
    async def refresh_cookies(self, interaction: discord.Interaction) -> None:
        """Refresh YouTube cookies from GSM. Owner-only command.
        
        Use this after updating the youtube-cookie secret in GSM.
        """
        # Check if user is the bot owner
        if not BOT_OWNER_ID:
            await interaction.response.send_message(
                "❌ `DISCORD_OWNER_ID` not configured. Cannot verify admin access.",
                ephemeral=True,
            )
            return
        
        try:
            owner_id = int(BOT_OWNER_ID)
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid `DISCORD_OWNER_ID` configuration.",
                ephemeral=True,
            )
            return
        
        if interaction.user.id != owner_id:
            await interaction.response.send_message(
                "❌ This command is restricted to the bot owner.",
                ephemeral=True,
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Delete cached cookies file
            if COOKIES_TEMP_FILE.exists():
                COOKIES_TEMP_FILE.unlink()
                logger.info("Deleted cached YouTube cookies")
            
            # Re-fetch from GSM
            new_path = await _fetch_cookies_from_gsm_async()
            
            if new_path and COOKIES_TEMP_FILE.exists():
                # Reset the YouTube client to pick up new cookies
                self.youtube = YouTubeAudioClient()
                
                # Reset cookie expiry notification flag
                self._cookie_expiry_notified = False
                
                embed = discord.Embed(
                    title="✅ Cookies Refreshed",
                    description=(
                        "Successfully fetched new YouTube cookies from Secret Manager.\n\n"
                        "The bot will now use the updated cookies for playback."
                    ),
                    color=discord.Color.green(),
                )
                embed.add_field(
                    name="Cache Location", 
                    value=f"`{COOKIES_TEMP_FILE}`", 
                    inline=False
                )
            else:
                embed = discord.Embed(
                    title="⚠️ No Cookies Found",
                    description=(
                        "Could not fetch cookies from Secret Manager.\n\n"
                        "Make sure the `youtube-cookie` secret exists and contains valid Netscape cookie data."
                    ),
                    color=discord.Color.orange(),
                )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error("Failed to refresh cookies", error=str(e))
            embed = discord.Embed(
                title="❌ Refresh Failed",
                description=f"Error: {str(e)}",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Load the cog.
    
    Args:
        bot: Discord bot instance
    """
    await bot.add_cog(MusicCog(bot))
