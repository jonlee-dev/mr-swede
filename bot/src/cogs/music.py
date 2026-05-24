"""/music * slash commands.

Channel-scoped to MUSIC_COMMAND_CHANNEL_ID via the `requires_channel`
decorator. All discord.py / wavelink interaction routes through
src.services.music; the cog itself is just slash-command plumbing
and embed rendering.

This cog ALSO owns the voice-gateway-recovery loop introduced
2026-05-13 (see PRD decision log). Two recovery signals:

  1. on_wavelink_websocket_closed -- handles clean voice WS closes
     (codes 4006/4014/4015 = server migration, transport reset).
  2. _voice_heartbeat asyncio.Task -- polls Lavalink every 2s for
     per-player state and aggregate frame deficit, feeds successive
     `VoiceHealthSnapshot`s into `should_recover`. Catches wedges
     that don't surface as Wavelink events (the 2026-05-12 Koe UDP
     reset shape).

Recovery is opinionated per the 2026-05-13 decision:
  - First wedge on a track   -> reconnect at saved position, post
                                visible "🔁 Audio dropped..." message.
  - Second wedge on same track -> skip, post visible "⏭️ Couldn't keep
                                    <track> playing..." message.

The decision function (`music.should_recover`) is a pure module-level
helper with full unit test coverage in tests/unit/test_voice_health.py;
this cog is the thin shell that wires it to live Discord I/O.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import discord
import wavelink
from discord import app_commands
from discord.ext import commands

from src.config.logging import get_logger
from src.config.secrets import get_secrets
from src.config.settings import get_settings
from src.services import music
from src.utils.checks import requires_channel, requires_guild

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Voice-gateway-recovery configuration
# ---------------------------------------------------------------------------

# How often the heartbeat task polls Lavalink's player-state endpoint
# per active player. With Lavalink's playerUpdateInterval bumped to 1s
# in server/lavalink/application.yml, 2s sampling gives us at least
# one playerUpdate per sample, so successive snapshots compare apples
# to apples. Detection window with _WEDGE_CONFIRMATION_SAMPLES=2 is
# ~4s of wedge before we fire.
_HEARTBEAT_INTERVAL_SECONDS = 2.0

# Discord voice WS close codes that mean "the voice connection died on
# the server side and we should try to recover" (vs being kicked or
# the user disconnecting, where we shouldn't fight). See the Discord
# Gateway docs for the full list.
#   4006: session_no_longer_valid    -- voice server lost our session
#   4014: disconnected               -- voice server went away (server migration)
#   4015: voice_server_crashed       -- voice server crashed; should reconnect
_RECOVERABLE_VOICE_CLOSE_CODES = frozenset({4006, 4014, 4015})


@dataclass
class _GuildRecoveryState:
    """Per-guild bookkeeping for the heartbeat. NOT frozen; the
    heartbeat mutates `last_snapshot`, `consecutive_wedge_samples`,
    `last_recovery_at`, and `recovery_attempts_for_track` on every
    tick.

    The whole struct is keyed by guild.id in MusicCog._recovery_state.
    Cleared whenever the player disconnects (cog stops tracking it).
    """

    last_snapshot: music.VoiceHealthSnapshot | None = None
    consecutive_wedge_samples: int = 0
    last_recovery_at: float | None = None
    # Reset when track_identifier changes. The recovery budget is
    # PER-TRACK: a wedged track gets one retry, then skipped.
    recovery_attempts_for_track: int = 0
    # Identifier of the track the attempts counter is bound to. When
    # we see a new identifier we zero `recovery_attempts_for_track`
    # before evaluating.
    attempts_bound_to_track: str | None = None


def _track_embed(track: music.TrackInfo, header: str, color: int = 0x1ABC9C) -> discord.Embed:
    embed = discord.Embed(title=header, description=f"**{track.title}**", color=color)
    embed.add_field(name="Artist", value=track.author, inline=True)
    embed.add_field(name="Length", value=music.format_duration(track.duration_ms), inline=True)
    if track.requester_id is not None:
        embed.add_field(name="Requested by", value=f"<@{track.requester_id}>", inline=True)
    if track.uri:
        embed.url = track.uri
    return embed


def _playlist_embed(result: music.PlayResult, color: int = 0x1ABC9C) -> discord.Embed:
    """Summary embed for a playlist/album URL resolution.

    Surfaces:
      - total tracks queued (= 1 first_track + extra_tracks_queued)
      - playlist title (or "playlist" fallback when lavasrc surfaces no name)
      - truncation warning when the source playlist exceeded
        PLAYLIST_TRACK_CAP
      - unresolved count when some tracks couldn't be matched
      - first-up track inline so the user sees what's playing now
    """
    assert result.first_track is not None  # caller checks
    total_queued = 1 + result.extra_tracks_queued
    title = result.playlist_title or "playlist"
    embed = discord.Embed(
        title=f"Queued {total_queued} tracks",
        description=f'From **"{title}"**',
        color=color,
    )

    embed.add_field(
        name="First up",
        value=f"**{result.first_track.title}** ({music.format_duration(result.first_track.duration_ms)})",
        inline=False,
    )

    if result.truncated_from is not None:
        embed.add_field(
            name="Truncated",
            value=(
                f"Playlist had {result.truncated_from} tracks; "
                f"queued the first {total_queued} (cap = {music.PLAYLIST_TRACK_CAP})."
            ),
            inline=False,
        )

    if result.unresolved_count > 0:
        embed.add_field(
            name="Unresolved",
            value=f"{result.unresolved_count} track(s) couldn't be resolved and were skipped.",
            inline=False,
        )

    if result.first_track.requester_id is not None:
        embed.set_footer(text=f"Requested by user {result.first_track.requester_id}")

    return embed


class MusicCog(commands.GroupCog, name="music"):
    """The /music command group."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._settings = get_settings()
        # Per-guild heartbeat bookkeeping. Lazily populated when the
        # heartbeat first sees a guild with an active player; entries
        # are cleared when a player disconnects.
        self._recovery_state: dict[int, _GuildRecoveryState] = {}
        # Set in cog_load, cancelled in cog_unload. The lifecycle is
        # tied to the cog (not the bot) so that test harnesses can
        # construct + tear down the cog without a long-running task
        # leaking.
        self._heartbeat_task: asyncio.Task[None] | None = None
        super().__init__()

    async def cog_load(self) -> None:
        """Spin up the voice-health heartbeat. Runs for the lifetime
        of the cog; cancelled in `cog_unload`.
        """
        self._heartbeat_task = asyncio.create_task(self._voice_heartbeat_loop())
        logger.info("Voice-health heartbeat started", interval_s=_HEARTBEAT_INTERVAL_SECONDS)

    async def cog_unload(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
            logger.info("Voice-health heartbeat stopped")

    # ------------------------------------------------------------------
    # Voice-gateway-recovery: event handlers
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_wavelink_websocket_closed(
        self, payload: wavelink.WebsocketClosedEventPayload
    ) -> None:
        """Discord voice WS closed on a player.

        We act on `_RECOVERABLE_VOICE_CLOSE_CODES` and ignore the rest
        (e.g., code 1000 = clean close from our own disconnect call;
        code 4014 with by_remote=False = user kicked the bot, where
        retrying would fight the user). Codes outside the recoverable
        set get logged but no action -- a quiet log line is more
        debuggable than a stack trace from a failed reconnect.
        """
        player = payload.player
        guild = getattr(player, "guild", None) if player is not None else None
        if guild is None:
            logger.debug(
                "wavelink websocket_closed: no guild on payload, ignoring",
                code=payload.code,
                reason=payload.reason,
            )
            return

        if payload.code not in _RECOVERABLE_VOICE_CLOSE_CODES:
            logger.info(
                "wavelink websocket_closed: non-recoverable code, no action",
                guild_id=guild.id,
                code=payload.code,
                reason=payload.reason,
                by_remote=payload.by_remote,
            )
            return

        logger.warning(
            "wavelink websocket_closed: recoverable close, attempting reconnect",
            guild_id=guild.id,
            code=payload.code,
            reason=payload.reason,
        )

        # Treat the event as a single confirmed wedge sample. Reuse
        # the same dispatcher that the heartbeat uses so the retry
        # budget + announcements stay consistent across signal sources.
        await self._dispatch_recovery(
            guild,
            player,
            trigger=f"wavelink_close_{payload.code}",
        )

    @commands.Cog.listener()
    async def on_wavelink_track_exception(
        self, payload: wavelink.TrackExceptionEventPayload
    ) -> None:
        """Lavalink reported an exception during playback (404, region
        lock, decode error, etc.). Wavelink will auto-advance the
        queue; we just surface a visible message and log.
        """
        track_title = getattr(payload.track, "title", "<unknown>") if payload.track else "<unknown>"
        exc_message = getattr(payload.exception, "message", repr(payload.exception))
        logger.warning(
            "wavelink track_exception",
            track=track_title,
            exception=exc_message,
        )
        guild = getattr(payload.player, "guild", None) if payload.player else None
        if guild is not None:
            await self._announce(
                guild,
                f"⚠️ Couldn't play **{track_title}** ({exc_message}). Skipping.",
            )

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: music.NodeReadyEventPayload) -> None:
        """Lavalink node finished its WS handshake. With Lavalink
        co-tenanted on the same VM (localhost), this fires once at
        bot startup -- AND any time we lose+regain the node WS. No
        action needed (Wavelink re-attaches players); just log so an
        operator scanning the journal can correlate reconnects to
        downstream issues.
        """
        logger.info(
            "wavelink node_ready",
            node_id=getattr(payload.node, "identifier", "<unknown>"),
            resumed=getattr(payload, "resumed", False),
        )

    # ------------------------------------------------------------------
    # Voice-gateway-recovery: heartbeat loop
    # ------------------------------------------------------------------

    async def _voice_heartbeat_loop(self) -> None:
        """Background task: every _HEARTBEAT_INTERVAL_SECONDS, sample
        every active player's voice health and feed the snapshot
        through `should_recover`.

        Designed to survive transient Lavalink errors -- any exception
        in a single tick is logged and the loop continues. Only
        CancelledError tears the loop down (from cog_unload).
        """
        try:
            # Brief settle delay so cog_load + bot.start don't race
            # the first tick against an unconnected gateway.
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
            while True:
                try:
                    await self._heartbeat_tick()
                except Exception as exc:  # noqa: BLE001 -- never let a single tick kill the loop
                    logger.error("voice heartbeat tick raised; continuing", error=repr(exc))
                await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise

    async def _heartbeat_tick(self) -> None:
        """One pass: for every guild with a connected wavelink.Player,
        fetch player state + aggregate frame deficit, build a snapshot,
        feed `should_recover`, dispatch on the result.
        """
        # We only have one Lavalink node (`_NODE_IDENTIFIER`), so fetch
        # the aggregate frame deficit once per tick. Per-player state
        # is fetched inside the loop because it includes a guild_id.
        node = wavelink.Pool.nodes.get("mr-swede-main")
        if node is None or node.status is not wavelink.NodeStatus.CONNECTED:
            return  # No node, no signal.

        node_uri = getattr(node, "uri", None)
        node_password = getattr(node, "password", None)
        session_id = getattr(node, "session_id", None)
        if not node_uri or not node_password or not session_id:
            return

        deficit = await music.fetch_aggregate_frame_deficit(node_uri, node_password)
        if deficit is None:
            return  # Don't penalize the wedge counter on a transient HTTP blip.

        now = time.monotonic()

        for guild in list(self.bot.guilds):
            player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
            if player is None:
                # Player disconnected -> drop any stale state we held.
                self._recovery_state.pop(guild.id, None)
                continue

            probe = await music.fetch_player_state(
                node_uri, node_password, session_id, guild.id
            )
            if probe is None:
                # Transient -- don't increment wedge counter.
                continue

            current_track = player.current
            track_identifier = getattr(current_track, "identifier", None)

            snapshot = music.VoiceHealthSnapshot(
                track_identifier=track_identifier,
                position_ms=probe.position_ms,
                voice_connected=probe.connected,
                frame_deficit=deficit,
                is_playing=bool(player.playing),
                is_paused=bool(player.paused),
                sampled_at=now,
            )

            state = self._recovery_state.setdefault(guild.id, _GuildRecoveryState())

            # If the track changed (or there's no track), reset the
            # per-track attempts counter. We do this BEFORE invoking
            # the decision so should_recover sees a clean budget for
            # the new track.
            if state.attempts_bound_to_track != track_identifier:
                state.attempts_bound_to_track = track_identifier
                state.recovery_attempts_for_track = 0
                state.consecutive_wedge_samples = 0

            # Increment the consecutive-wedge counter based on this
            # snapshot's signal, BEFORE calling should_recover. The
            # function checks `consecutive_wedge_samples >= 2`.
            voice_dead = not snapshot.voice_connected
            deficit_grew = (
                state.last_snapshot is not None
                and snapshot.is_playing
                and not snapshot.is_paused
                and snapshot.track_identifier is not None
                and state.last_snapshot.track_identifier == snapshot.track_identifier
                and (snapshot.frame_deficit - state.last_snapshot.frame_deficit) >= 25
            )
            sample_wedged = voice_dead or deficit_grew
            if sample_wedged and snapshot.is_playing and not snapshot.is_paused:
                state.consecutive_wedge_samples += 1
            else:
                state.consecutive_wedge_samples = 0

            action = music.should_recover(
                curr=snapshot,
                prev=state.last_snapshot,
                consecutive_wedge_samples=state.consecutive_wedge_samples,
                last_recovery_at=state.last_recovery_at,
                recovery_attempts_for_track=state.recovery_attempts_for_track,
                now=now,
            )

            # Always update last_snapshot AFTER the decision so the
            # next tick has the right prev. Update before dispatching
            # the recovery so it sees the latest state.
            state.last_snapshot = snapshot

            if action is music.RecoveryAction.NONE:
                continue

            logger.warning(
                "voice heartbeat: wedge detected",
                guild_id=guild.id,
                track=getattr(current_track, "title", "<unknown>"),
                action=action.value,
                consecutive_wedge_samples=state.consecutive_wedge_samples,
                attempts_for_track=state.recovery_attempts_for_track,
            )

            await self._dispatch_recovery(
                guild,
                player,
                trigger="heartbeat",
                preselected_action=action,
            )

    # ------------------------------------------------------------------
    # Voice-gateway-recovery: dispatcher
    # ------------------------------------------------------------------

    async def _dispatch_recovery(
        self,
        guild: discord.Guild,
        player: wavelink.Player,
        trigger: str,
        preselected_action: music.RecoveryAction | None = None,
    ) -> None:
        """Translate a RecoveryAction into Discord side effects.

        For the heartbeat caller, `preselected_action` is the result
        of should_recover. For the event-handler caller (which fires
        on a CONFIRMED wedge close code), we pick between RECOVER
        and GIVE_UP_AND_SKIP locally based on the per-track attempts
        budget so both paths stay consistent.
        """
        state = self._recovery_state.setdefault(guild.id, _GuildRecoveryState())
        current_track = player.current

        # Bind attempts counter to the current track if not already.
        # Defensive: the heartbeat normally does this, but the event
        # handler can fire before the heartbeat has run once.
        track_identifier = getattr(current_track, "identifier", None)
        if state.attempts_bound_to_track != track_identifier:
            state.attempts_bound_to_track = track_identifier
            state.recovery_attempts_for_track = 0

        if preselected_action is None:
            # Event-handler caller: derive action from the budget.
            if state.recovery_attempts_for_track >= 1:
                action = music.RecoveryAction.GIVE_UP_AND_SKIP
            else:
                action = music.RecoveryAction.RECOVER
        else:
            action = preselected_action

        if action is music.RecoveryAction.NONE:
            return

        track_title = getattr(current_track, "title", "<unknown>") if current_track else "<unknown>"

        if action is music.RecoveryAction.GIVE_UP_AND_SKIP:
            await self._announce(
                guild,
                (
                    f"⏭️ Couldn't keep **{track_title}** playing "
                    "(audio dropped after the retry). Skipping."
                ),
            )
            try:
                await player.skip(force=True)
            except Exception as exc:  # noqa: BLE001 -- last-resort branch; log + move on
                logger.error("give-up: player.skip raised", error=repr(exc))
            # Reset counter so the NEXT track gets its own budget.
            state.recovery_attempts_for_track = 0
            state.attempts_bound_to_track = None
            state.consecutive_wedge_samples = 0
            return

        # RecoveryAction.RECOVER path.
        position_ms = player.position if current_track else 0
        await self._announce(
            guild,
            (
                f"🔁 Audio dropped on **{track_title}**, reconnecting at "
                f"{music.format_duration(position_ms)}…"
            ),
        )
        # Bump the counter BEFORE attempting reconnect so a concurrent
        # heartbeat tick can't double-fire.
        state.recovery_attempts_for_track += 1
        state.last_recovery_at = time.monotonic()
        state.consecutive_wedge_samples = 0

        voice_channel = getattr(player, "channel", None)
        if voice_channel is None:
            logger.error(
                "recovery dispatch: player has no voice channel, can't reconnect",
                guild_id=guild.id,
                trigger=trigger,
            )
            await self._announce(
                guild,
                f"❌ Couldn't reconnect to voice (no channel). Use `/music play` to retry.",
            )
            return

        ok = await music.reconnect_player_at_position(player, voice_channel)
        if not ok:
            await self._announce(
                guild,
                "❌ Reconnect failed. Try `/music stop` then `/music play` again.",
            )

    async def _announce(self, guild: discord.Guild, message: str) -> None:
        """Post a recovery / failure message to the music channel.

        Uses MUSIC_COMMAND_CHANNEL_ID (the same channel `/music *` is
        scoped to). If unset, or the channel can't be resolved /
        doesn't allow sends, the message is logged at info level so
        an operator can still correlate without it being a hard error.
        """
        channel_id_str = self._settings.music_command_channel_id
        if not channel_id_str:
            logger.info("recovery announce (no channel configured)", message=message)
            return
        try:
            channel_id = int(channel_id_str)
        except ValueError:
            logger.warning(
                "MUSIC_COMMAND_CHANNEL_ID is not an integer, skipping announce",
                value=channel_id_str,
            )
            return
        channel = guild.get_channel(channel_id) or self.bot.get_channel(channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            logger.warning(
                "recovery announce: channel not found or not a TextChannel",
                channel_id=channel_id,
                guild_id=guild.id,
            )
            return
        try:
            await channel.send(message)
        except discord.HTTPException as exc:
            logger.warning(
                "recovery announce: channel.send failed",
                channel_id=channel_id,
                error=repr(exc),
            )

    # ------------------------------------------------------------------
    # Slash commands (unchanged below this line)
    # ------------------------------------------------------------------

    async def _ensure_node_connected(self) -> bool:
        """Open the Wavelink WebSocket to localhost Lavalink if not already
        open. Returns True on success, False on any failure (cog surfaces
        a generic error to the user).

        Pre-bot-vm-migration this used to do a GCE start dance for a
        standalone Lavalink VM. Lavalink now co-tenants the bot's VM at
        localhost:2333, so the host is fixed and the only work is the
        Wavelink handshake.
        """
        password = get_secrets(self._settings.discord_bot_name).lavalink_password
        if not password:
            logger.error("No Lavalink password available; cannot connect to node")
            return False
        host = self._settings.lavalink_host or "localhost"
        try:
            await music.connect_node(self.bot, host, self._settings.lavalink_port, password)
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

        if not await self._ensure_node_connected():
            await interaction.followup.send(
                "Couldn't connect to the music server. Check `/health` and the bot logs.",
                ephemeral=True,
            )
            return

        try:
            result = await music.play(member.voice.channel, query, requester_id=interaction.user.id)
        except Exception as e:
            logger.error("play failed", query=query, error=str(e))
            await interaction.followup.send(f"Couldn't play that: `{e}`", ephemeral=True)
            return

        if result.first_track is None:
            await interaction.followup.send(f"No results for `{query}`.", ephemeral=True)
            return

        # Branch on result shape: playlist URLs render the summary embed,
        # search/single-track results render the existing per-track embed.
        if result.playlist_title is not None:
            await interaction.followup.send(embed=_playlist_embed(result))
            return

        header = (
            "Now playing"
            if result.first_track_queue_position == 0
            else f"Queued (#{result.first_track_queue_position})"
        )
        await interaction.followup.send(embed=_track_embed(result.first_track, header))

    @app_commands.command(name="skip", description="Skip the currently playing track")
    @requires_channel("music_command_channel_id")
    @requires_guild
    async def skip(self, interaction: discord.Interaction) -> None:
        if await music.skip(interaction.guild):
            await interaction.followup.send("Skipped.")
        else:
            await interaction.followup.send("Nothing is playing.", ephemeral=True)

    @app_commands.command(name="pause", description="Pause playback")
    @requires_channel("music_command_channel_id")
    @requires_guild
    async def pause(self, interaction: discord.Interaction) -> None:
        if await music.pause(interaction.guild):
            await interaction.followup.send("Paused.")
        else:
            await interaction.followup.send("Not connected to voice.", ephemeral=True)

    @app_commands.command(name="resume", description="Resume playback")
    @requires_channel("music_command_channel_id")
    @requires_guild
    async def resume(self, interaction: discord.Interaction) -> None:
        if await music.resume(interaction.guild):
            await interaction.followup.send("Resumed.")
        else:
            await interaction.followup.send("Not connected to voice.", ephemeral=True)

    @app_commands.command(name="stop", description="Stop playback, clear queue, leave voice")
    @requires_channel("music_command_channel_id")
    @requires_guild
    async def stop(self, interaction: discord.Interaction) -> None:
        if await music.stop_and_disconnect(interaction.guild):
            await interaction.followup.send("Stopped and disconnected.")
        else:
            await interaction.followup.send("Already stopped.", ephemeral=True)

    @app_commands.command(name="queue", description="Show the next ~10 queued tracks")
    @requires_channel("music_command_channel_id")
    @requires_guild
    async def queue(self, interaction: discord.Interaction) -> None:
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
    @requires_guild
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        current = music.now_playing(interaction.guild)
        if current is None:
            await interaction.followup.send("Nothing is playing.")
            return
        await interaction.followup.send(embed=_track_embed(current, "Now playing"))

    @app_commands.command(name="volume", description="Set per-server playback volume (0-200)")
    @app_commands.describe(level="Volume percentage. 100 = normal, 200 = loud, 0 = mute.")
    @requires_channel("music_command_channel_id")
    @requires_guild
    async def volume(self, interaction: discord.Interaction, level: int) -> None:
        if await music.set_volume(interaction.guild, level):
            await interaction.followup.send(f"Volume set to {max(0, min(200, level))}%.")
        else:
            await interaction.followup.send("Not connected to voice.", ephemeral=True)

    @app_commands.command(name="shuffle", description="Shuffle the queue in place")
    @requires_channel("music_command_channel_id")
    @requires_guild
    async def shuffle(self, interaction: discord.Interaction) -> None:
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
    @requires_guild
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        if music.set_loop(interaction.guild, mode.value):
            await interaction.followup.send(f"Loop mode: **{mode.value}**.")
        else:
            await interaction.followup.send("Not connected to voice.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCog(bot))
