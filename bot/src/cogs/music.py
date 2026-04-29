"""/music * slash commands.

Channel-scoped to MUSIC_COMMAND_CHANNEL_ID via the `requires_channel`
decorator. All discord.py / wavelink interaction routes through
src.services.music; the cog itself is just slash-command plumbing
and embed rendering.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.config.logging import get_logger
from src.config.secrets import get_secrets
from src.config.settings import get_settings
from src.services import compute, music
from src.utils.checks import requires_channel

logger = get_logger(__name__)


VM_START_TIMEOUT_SECONDS = 90


def _track_embed(track: music.TrackInfo, header: str, color: int = 0x1ABC9C) -> discord.Embed:
    embed = discord.Embed(title=header, description=f"**{track.title}**", color=color)
    embed.add_field(name="Artist", value=track.author, inline=True)
    embed.add_field(name="Length", value=music.format_duration(track.duration_ms), inline=True)
    if track.requester_id is not None:
        embed.add_field(name="Requested by", value=f"<@{track.requester_id}>", inline=True)
    if track.uri:
        embed.url = track.uri
    return embed


class MusicCog(commands.GroupCog, name="music"):
    """The /music command group."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._settings = get_settings()
        super().__init__()

    async def _ensure_lavalink_running(self, interaction: discord.Interaction) -> str | None:
        """Make sure the Lavalink VM is RUNNING and reachable. Returns the
        Lavalink host on success, None on timeout (and replies to the
        user via followup with a wait-and-retry message).
        """
        s = self._settings
        # If a host override is set (local dev with localhost Lavalink),
        # skip the GCE start dance.
        if s.lavalink_host:
            return s.lavalink_host

        state = await compute.describe_instance(
            s.gcp_project_id, s.lavalink_zone, s.lavalink_instance_name
        )
        if state.status != "RUNNING":
            await interaction.followup.send(
                "Starting the music server, give it ~90 seconds and try again."
            )
            await compute.start_instance(
                s.gcp_project_id, s.lavalink_zone, s.lavalink_instance_name
            )
            return None

        if not state.public_ip:
            await interaction.followup.send(
                "Music server is RUNNING but doesn't have a public IP yet. Try again in a moment."
            )
            return None

        return state.public_ip

    async def _ensure_node_connected(self, host: str) -> bool:
        """Open the Wavelink WebSocket if not already open."""
        password = get_secrets(self._settings.discord_bot_name).lavalink_password
        if not password:
            logger.error("No Lavalink password available; cannot connect to node")
            return False
        try:
            await music.connect_node(host, self._settings.lavalink_port, password)
            return True
        except Exception as e:
            logger.error("Failed to connect Lavalink node", error=str(e))
            return False

    @app_commands.command(name="play", description="Search and play a song or paste a URL")
    @app_commands.describe(query="YouTube search query or direct URL")
    @requires_channel("music_command_channel_id")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(thinking=True)

        # User must already be in a voice channel for us to know where
        # to play. We don't pull them into one ourselves.
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None or member.voice is None or member.voice.channel is None:
            await interaction.followup.send(
                "Join a voice channel first, then re-run the command.", ephemeral=True
            )
            return

        host = await self._ensure_lavalink_running(interaction)
        if host is None:
            return  # _ensure_lavalink_running already sent a reply
        if not await self._ensure_node_connected(host):
            await interaction.followup.send(
                "Couldn't connect to the music server. Check `/health` and the bot logs.",
                ephemeral=True,
            )
            return

        try:
            track, queue_pos = await music.play(
                member.voice.channel, query, requester_id=interaction.user.id
            )
        except Exception as e:
            logger.error("play failed", query=query, error=str(e))
            await interaction.followup.send(f"Couldn't play that: `{e}`", ephemeral=True)
            return

        if track is None:
            await interaction.followup.send(f"No results for `{query}`.", ephemeral=True)
            return

        header = "Now playing" if queue_pos == 0 else f"Queued (#{queue_pos})"
        await interaction.followup.send(embed=_track_embed(track, header))

    @app_commands.command(name="skip", description="Skip the currently playing track")
    @requires_channel("music_command_channel_id")
    async def skip(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        if interaction.guild is None:
            await interaction.followup.send("Use this in a server channel.", ephemeral=True)
            return
        if await music.skip(interaction.guild):
            await interaction.followup.send("Skipped.")
        else:
            await interaction.followup.send("Nothing is playing.", ephemeral=True)

    @app_commands.command(name="pause", description="Pause playback")
    @requires_channel("music_command_channel_id")
    async def pause(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        if interaction.guild is None:
            await interaction.followup.send("Use this in a server channel.", ephemeral=True)
            return
        if await music.pause(interaction.guild):
            await interaction.followup.send("Paused.")
        else:
            await interaction.followup.send("Not connected to voice.", ephemeral=True)

    @app_commands.command(name="resume", description="Resume playback")
    @requires_channel("music_command_channel_id")
    async def resume(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        if interaction.guild is None:
            await interaction.followup.send("Use this in a server channel.", ephemeral=True)
            return
        if await music.resume(interaction.guild):
            await interaction.followup.send("Resumed.")
        else:
            await interaction.followup.send("Not connected to voice.", ephemeral=True)

    @app_commands.command(name="stop", description="Stop playback, clear queue, leave voice")
    @requires_channel("music_command_channel_id")
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        if interaction.guild is None:
            await interaction.followup.send("Use this in a server channel.", ephemeral=True)
            return
        if await music.stop_and_disconnect(interaction.guild):
            await interaction.followup.send("Stopped and disconnected.")
        else:
            await interaction.followup.send("Already stopped.", ephemeral=True)

    @app_commands.command(name="queue", description="Show the next ~10 queued tracks")
    @requires_channel("music_command_channel_id")
    async def queue(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        if interaction.guild is None:
            await interaction.followup.send("Use this in a server channel.", ephemeral=True)
            return
        current = music.now_playing(interaction.guild)
        upcoming = music.queue_snapshot(interaction.guild, limit=10)

        if current is None and not upcoming:
            await interaction.followup.send("Queue is empty and nothing is playing.")
            return

        embed = discord.Embed(title="Music queue", color=0x1ABC9C)
        if current is not None:
            embed.add_field(
                name="Now playing",
                value=f"**{current.title}** ({music.format_duration(current.duration_ms)})",
                inline=False,
            )
        if upcoming:
            lines = [
                f"{i + 1}. **{t.title}** ({music.format_duration(t.duration_ms)})"
                for i, t in enumerate(upcoming)
            ]
            embed.add_field(name=f"Up next ({len(upcoming)})", value="\n".join(lines), inline=False)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="nowplaying", description="Show the current track")
    @requires_channel("music_command_channel_id")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        if interaction.guild is None:
            await interaction.followup.send("Use this in a server channel.", ephemeral=True)
            return
        current = music.now_playing(interaction.guild)
        if current is None:
            await interaction.followup.send("Nothing is playing.")
            return
        await interaction.followup.send(embed=_track_embed(current, "Now playing"))

    @app_commands.command(name="volume", description="Set per-server playback volume (0-200)")
    @app_commands.describe(level="Volume percentage. 100 = normal, 200 = loud, 0 = mute.")
    @requires_channel("music_command_channel_id")
    async def volume(self, interaction: discord.Interaction, level: int) -> None:
        await interaction.response.defer(thinking=True)
        if interaction.guild is None:
            await interaction.followup.send("Use this in a server channel.", ephemeral=True)
            return
        if await music.set_volume(interaction.guild, level):
            await interaction.followup.send(f"Volume set to {max(0, min(200, level))}%.")
        else:
            await interaction.followup.send("Not connected to voice.", ephemeral=True)

    @app_commands.command(name="shuffle", description="Shuffle the queue in place")
    @requires_channel("music_command_channel_id")
    async def shuffle(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        if interaction.guild is None:
            await interaction.followup.send("Use this in a server channel.", ephemeral=True)
            return
        n = await music.shuffle(interaction.guild)
        if n == 0:
            await interaction.followup.send("Queue is empty.", ephemeral=True)
        else:
            await interaction.followup.send(f"Shuffled {n} track(s).")

    @app_commands.command(name="loop", description="Loop mode: off, track, or queue")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="off", value="off"),
            app_commands.Choice(name="track", value="track"),
            app_commands.Choice(name="queue", value="queue"),
        ]
    )
    @requires_channel("music_command_channel_id")
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        await interaction.response.defer(thinking=True)
        if interaction.guild is None:
            await interaction.followup.send("Use this in a server channel.", ephemeral=True)
            return
        if music.set_loop(interaction.guild, mode.value):
            await interaction.followup.send(f"Loop mode: **{mode.value}**.")
        else:
            await interaction.followup.send("Not connected to voice.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCog(bot))


# Suppress "imported but unused" for the timeout constant -- the cog
# body could end up using it for asyncio.wait_for in a future PR.
_ = VM_START_TIMEOUT_SECONDS
