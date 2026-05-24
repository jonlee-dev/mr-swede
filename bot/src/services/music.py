"""Music service: thin wrapper over wavelink for Lavalink-backed audio.

Owns the Lavalink node lifecycle and exposes the operations the cog
uses. The cog never imports `wavelink` directly -- everything routes
through this module so:

  - The wavelink dependency stays pinned in one place
  - Tests of the cog can mock this module without library knowledge
  - Future swap to a different Lavalink client (Pomice, Mafic) is
    contained to this file

Most of the surface (play/queue/pause/resume/skip/volume/shuffle/loop)
is thin glue over wavelink and is exercised via live integration
against a real Lavalink rather than unit-tested. The voice-recovery
helpers (`should_recover`, `VoiceHealthSnapshot`, ...) ARE pure and
unit-tested in tests/unit/test_voice_health.py.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any

import discord
import httpx
import wavelink

from src.config.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class TrackInfo:
    """A snapshot of a queued / now-playing track for embed rendering."""

    title: str
    author: str
    duration_ms: int
    uri: str | None
    requester_id: int | None  # Discord user ID of who queued it


# Hard cap on how many tracks a single playlist URL can enqueue. Set to
# 100 to comfortably cover normal Spotify/YouTube playlists while
# preventing a runaway 5000-track YouTube auto-mix from filling the
# queue. If a playlist exceeds this, we keep the first PLAYLIST_TRACK_CAP
# tracks and surface "truncated to N/M" in the embed.
PLAYLIST_TRACK_CAP = 100


@dataclass(frozen=True)
class PlayResult:
    """The shape returned by `play()` for either single tracks or
    playlist/album URL resolutions.

    Discriminated by `playlist_title`: None means a single-track
    resolution (or a search query), non-None means the input URL
    resolved to a multi-track playlist or album.

    The cog uses this to pick between the single-track embed and the
    playlist-summary embed without itself knowing whether the resolver
    returned a `wavelink.Playlist` or a `Playable`.
    """

    # First track to play / first track that landed in the queue.
    # None only when the query resolved to zero tracks (no results).
    first_track: TrackInfo | None

    # 0 when first_track is now playing (queue was empty); otherwise
    # the 1-based position of first_track in the queue.
    first_track_queue_position: int

    # Playlist metadata. None for single-track / search-query results.
    playlist_title: str | None

    # Number of tracks added BEYOND first_track. 0 for single tracks.
    # Capped at PLAYLIST_TRACK_CAP - 1 (since first_track is the +1).
    extra_tracks_queued: int

    # When the playlist URL resolved to MORE tracks than we accepted.
    # Allows the cog to surface "truncated to 100/523".
    truncated_from: int | None

    # When some tracks in the playlist failed to resolve (lavasrc
    # couldn't find a YouTube match, region locked, deleted, etc.).
    # `extra_tracks_queued` already excludes these; `unresolved_count`
    # is purely for the "N tracks couldn't be resolved" surface.
    unresolved_count: int


def _to_track_info(track: wavelink.Playable, requester_id: int | None = None) -> TrackInfo:
    return TrackInfo(
        title=track.title,
        author=track.author or "unknown",
        duration_ms=track.length,
        uri=getattr(track, "uri", None),
        requester_id=requester_id,
    )


_NODE_IDENTIFIER = "mr-swede-main"

# Wavelink's WS handshake against a healthy localhost Lavalink finishes
# in well under a second, but Lavalink itself can take 60-90s to boot
# on a fresh VM (plugin downloads + JVM startup). Keep 90s so the cog
# surfaces a useful "Lavalink isn't up yet" error instead of hanging
# forever, AND tolerates the rare case where bot.service races
# lavalink.service post-reboot.
_CONNECT_TIMEOUT_SECONDS = 90.0

_CONNECT_POLL_INTERVAL_SECONDS = 0.5
_HEALTH_CHECK_TIMEOUT_SECONDS = 3.0


async def connect_node(client: discord.Client, host: str, port: int, password: str) -> None:
    """Open the WebSocket to Lavalink and wait until the node is CONNECTED.

    Idempotent: if the cached node entry is already CONNECTED, returns
    immediately. Otherwise evicts whatever's there and reconnects.

    Wavelink 3.x gotchas this hides:
      - `Pool.connect()` returns BEFORE the node finishes its
        handshake. We poll `node.status` until CONNECTED.
      - The pool refuses a `connect()` if the identifier is already
        in use, even with a stale entry. We `node.close(eject=True)`
        which removes the entry from the real `_Pool__nodes` dict
        (the 2026-05-10 lesson -- see PRD decision log).
    """
    uri = f"http://{host}:{port}"

    existing = wavelink.Pool.nodes.get(_NODE_IDENTIFIER)
    if existing is not None:
        if existing.status is wavelink.NodeStatus.CONNECTED:
            logger.debug("Lavalink node already connected", uri=uri)
            return
        logger.info(
            "Cached Lavalink node not usable; evicting and reconnecting",
            cached_status=str(existing.status),
            new_uri=uri,
        )
        try:
            await existing.close(eject=True)
        except Exception as exc:  # noqa: BLE001 -- best-effort cleanup
            logger.warning("node.close(eject=True) raised during eviction", error=repr(exc))

    logger.info("Connecting to Lavalink node", uri=uri)
    node = wavelink.Node(uri=uri, password=password, identifier=_NODE_IDENTIFIER)
    await wavelink.Pool.connect(client=client, nodes=[node])

    # Poll until CONNECTED. Bail with TimeoutError if it doesn't
    # happen within the deadline so the cog surfaces a useful error
    # instead of hanging forever.
    deadline = asyncio.get_event_loop().time() + _CONNECT_TIMEOUT_SECONDS
    while asyncio.get_event_loop().time() < deadline:
        current = wavelink.Pool.nodes.get(_NODE_IDENTIFIER, node)
        if current.status is wavelink.NodeStatus.CONNECTED:
            logger.info("Lavalink node connected", uri=uri)
            return
        await asyncio.sleep(_CONNECT_POLL_INTERVAL_SECONDS)

    logger.error("Lavalink node did not reach CONNECTED state", uri=uri)
    raise TimeoutError(
        f"Lavalink node {_NODE_IDENTIFIER} did not connect within {_CONNECT_TIMEOUT_SECONDS}s"
    )


async def play(
    voice_channel: discord.VoiceChannel | discord.StageChannel,
    query: str,
    requester_id: int | None = None,
) -> PlayResult:
    """Resolve `query` (search string OR URL), enqueue the result, start
    playback if nothing is playing.

    Three resolution modes:

      1. Search query -> single Playable -> single-track result
      2. Track URL    -> single Playable -> single-track result
      3. Playlist/album URL -> wavelink.Playlist -> multi-track result

    Mode 3 is the v4.2 work: we iterate the playlist, enqueue every
    track up to PLAYLIST_TRACK_CAP, and surface the truncation /
    unresolved counts in the returned PlayResult so the cog can render
    a useful summary embed.

    Joins `voice_channel` if the player isn't already connected.
    Returns PlayResult with first_track=None when nothing resolved.
    """
    guild = voice_channel.guild
    player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
    if player is None:
        player = await voice_channel.connect(cls=wavelink.Player)
    elif player.channel != voice_channel:
        await player.move_to(voice_channel)

    # Wavelink 3.x defaults Player.autoplay to AutoPlayMode.disabled,
    # which means a track ending does NOT trigger the next queue item.
    # We want "partial" -- advance the queue automatically, but DON'T
    # fetch related-song recommendations from YouTube (that's the
    # `enabled` mode and would surprise users by playing forever).
    # Idempotent: setting on every play() call is harmless and cheap.
    player.autoplay = wavelink.AutoPlayMode.partial

    # wavelink.Playable.search defaults to `ytmsearch:` (YouTube Music)
    # when no prefix is present. Don't prepend `ytsearch:` ourselves --
    # wavelink would treat the whole thing as a literal search string
    # ("ytmsearch:ytsearch:hi") and resolve to nonsense. URLs (incl.
    # Spotify URLs once lavasrc is loaded server-side) pass through
    # unmodified.
    tracks: Any = await wavelink.Playable.search(query)
    if not tracks:
        return PlayResult(
            first_track=None,
            first_track_queue_position=0,
            playlist_title=None,
            extra_tracks_queued=0,
            truncated_from=None,
            unresolved_count=0,
        )

    if isinstance(tracks, wavelink.Playlist):
        return await _enqueue_playlist(player, tracks, requester_id)

    return await _enqueue_single(player, tracks[0], requester_id)


async def _enqueue_single(
    player: wavelink.Player,
    track: wavelink.Playable,
    requester_id: int | None,
) -> PlayResult:
    """Enqueue (or play directly if idle) a single resolved track."""
    if not player.playing and player.queue.is_empty:
        await player.play(track)
        return PlayResult(
            first_track=_to_track_info(track, requester_id),
            first_track_queue_position=0,
            playlist_title=None,
            extra_tracks_queued=0,
            truncated_from=None,
            unresolved_count=0,
        )

    player.queue.put(track)
    return PlayResult(
        first_track=_to_track_info(track, requester_id),
        first_track_queue_position=len(player.queue),
        playlist_title=None,
        extra_tracks_queued=0,
        truncated_from=None,
        unresolved_count=0,
    )


async def _enqueue_playlist(
    player: wavelink.Player,
    playlist: wavelink.Playlist,
    requester_id: int | None,
) -> PlayResult:
    """Enqueue every track from a resolved playlist/album, applying the
    PLAYLIST_TRACK_CAP and skipping any nulls lavasrc may surface for
    unresolvable tracks (region-locked, deleted, no YouTube match).

    Lavasrc populates the playlist with `Playable` entries it has
    already matched to a real YouTube source. Tracks it CAN'T match
    are dropped from `playlist.tracks` upstream, BUT the playlist's
    advertised total may exceed `len(playlist.tracks)`. We don't have
    a clean signal for that delta from wavelink today, so
    `unresolved_count` is computed conservatively as 0 for now and
    revisited if we ever hit a metadata source that exposes per-track
    resolution status.
    """
    raw_tracks: list[wavelink.Playable] = list(playlist.tracks)
    total_in_playlist = len(raw_tracks)

    truncated_from: int | None = None
    if total_in_playlist > PLAYLIST_TRACK_CAP:
        truncated_from = total_in_playlist
        raw_tracks = raw_tracks[:PLAYLIST_TRACK_CAP]

    if not raw_tracks:
        return PlayResult(
            first_track=None,
            first_track_queue_position=0,
            playlist_title=getattr(playlist, "name", None) or "playlist",
            extra_tracks_queued=0,
            truncated_from=truncated_from,
            unresolved_count=0,
        )

    first = raw_tracks[0]
    rest = raw_tracks[1:]

    # First track: play directly if idle, else enqueue at the tail.
    if not player.playing and player.queue.is_empty:
        await player.play(first)
        first_position = 0
    else:
        player.queue.put(first)
        first_position = len(player.queue)

    # Append the rest in order.
    for t in rest:
        player.queue.put(t)

    return PlayResult(
        first_track=_to_track_info(first, requester_id),
        first_track_queue_position=first_position,
        playlist_title=getattr(playlist, "name", None) or "playlist",
        extra_tracks_queued=len(rest),
        truncated_from=truncated_from,
        unresolved_count=0,
    )


async def skip(guild: discord.Guild) -> bool:
    """Skip the current track. Returns False if nothing was playing."""
    player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
    if player is None or not player.playing:
        return False
    await player.skip(force=True)
    return True


async def pause(guild: discord.Guild) -> bool:
    player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
    if player is None:
        return False
    await player.pause(True)
    return True


async def resume(guild: discord.Guild) -> bool:
    player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
    if player is None:
        return False
    await player.pause(False)
    return True


async def stop_and_disconnect(guild: discord.Guild) -> bool:
    """Stop playback, clear the queue, leave voice. Idempotent."""
    player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
    if player is None:
        return False
    player.queue.clear()
    await player.disconnect()
    return True


def now_playing(guild: discord.Guild) -> TrackInfo | None:
    """Return the currently playing track, or None."""
    player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
    if player is None or player.current is None:
        return None
    return _to_track_info(player.current)


def queue_snapshot(guild: discord.Guild, limit: int = 10) -> list[TrackInfo]:
    """Return up to `limit` tracks from the head of the queue (FIFO order)."""
    player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
    if player is None:
        return []
    out: list[TrackInfo] = []
    for i, track in enumerate(player.queue):
        if i >= limit:
            break
        out.append(_to_track_info(track))
    return out


async def set_volume(guild: discord.Guild, volume_percent: int) -> bool:
    """Set per-player volume. wavelink accepts 0-1000; we clamp to 0-200."""
    player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
    if player is None:
        return False
    clamped = max(0, min(200, volume_percent))
    await player.set_volume(clamped)
    return True


async def shuffle(guild: discord.Guild) -> int:
    """Shuffle the queue in place. Returns the number of tracks shuffled."""
    player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
    if player is None:
        return 0
    player.queue.shuffle()
    return len(player.queue)


def set_loop(guild: discord.Guild, mode: str) -> bool:
    """Set loop mode: 'off' | 'track' | 'queue'."""
    player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
    if player is None:
        return False
    if mode == "off":
        player.queue.mode = wavelink.QueueMode.normal
    elif mode == "track":
        player.queue.mode = wavelink.QueueMode.loop
    elif mode == "queue":
        player.queue.mode = wavelink.QueueMode.loop_all
    else:
        return False
    return True


def format_duration(ms: int) -> str:
    """Format milliseconds as M:SS or H:MM:SS."""
    total_seconds = ms // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


# ---------------------------------------------------------------------------
# Voice-gateway-recovery primitives (2026-05-13)
# ---------------------------------------------------------------------------
#
# Background. The 2026-05-12 incident: mid-session, Discord's voice
# server reset the UDP connection (Koe logged `recvAddress(..) failed
# with error(-104): Connection reset by peer`). Lavalink kept advancing
# the player's internal position as if audio were still flowing, so
# the bot had no way to detect the wedge from `player.position` alone.
# Users heard silence for ~3min before manually running `/music stop`.
#
# This block implements bot-side recovery. Two signals feed the
# decision function below:
#
#   1. Event-driven (`on_wavelink_websocket_closed` in cogs/music.py)
#      catches CLEAN voice gateway closes (codes 4006/4014/4015 — server
#      migration, transport reset that took the WS down with it). This
#      is the common case but NOT guaranteed to fire for every wedge
#      shape we've seen.
#
#   2. Heartbeat-driven (2s cadence in cogs/music.py) polls Lavalink's
#      per-player state via `fetch_player_state` and feeds successive
#      `VoiceHealthSnapshot`s into `should_recover`. Catches wedges
#      that don't surface as Wavelink events.
#
# `should_recover` is a pure function over snapshot history and a
# per-track attempt counter; unit-tested in tests/unit/test_voice_health.py.
# The cog wraps it with side effects (reconnect / skip / post message).


# Heartbeat samples must show this many consecutive "wedged" reads
# before we act. With a 2s cadence and Lavalink's playerUpdate at 1s,
# 2 stale samples = ~4s of confirmed wedge before we fire recovery.
# Keeps single-frame UDP jitter from triggering a recovery loop while
# still catching real wedges fast.
_WEDGE_CONFIRMATION_SAMPLES = 2

# Per-track recovery budget. We attempt to reconnect at the saved
# position exactly once per track; if the SAME track wedges again, we
# skip it instead of spinning in a reconnect loop. Decision rationale
# documented in docs/prd.md decision log 2026-05-13.
_MAX_RECOVERY_ATTEMPTS_PER_TRACK = 1

# After a recovery attempt, ignore wedge signals for this long before
# considering another action. Protects against the heartbeat firing
# on a still-reconnecting voice link.
_RECOVERY_THROTTLE_SECONDS = 60.0

# Minimum frame deficit growth (frames per sample) to count as a
# wedge signal from the aggregate /v4/stats endpoint. Lavalink runs
# at 50 frames/sec, so >=25 over a 2s sample means roughly half the
# expected frames went undelivered.
_DEFICIT_GROWTH_THRESHOLD = 25


class RecoveryAction(str, Enum):
    """Outcome of a `should_recover()` call.

    Pure-data return so the cog can dispatch (post message + reconnect
    vs post message + skip) without `should_recover` needing to know
    about Discord channels.
    """

    NONE = "none"
    RECOVER = "recover"
    GIVE_UP_AND_SKIP = "give_up_and_skip"


@dataclass(frozen=True)
class VoiceHealthSnapshot:
    """Inputs to `should_recover`. Mirrors the `_LivenessSnapshot`
    pattern in src.http: decouple the decision logic from any live
    Wavelink/Lavalink state so every branch is unit-testable without
    a real player.

    `track_identifier` is wavelink's per-Playable id (or None when no
    track is loaded). We use it to detect track transitions: if the
    identifier changed between samples, we reset the per-track wedge
    state so a long queue doesn't carry attempts forward.

    `voice_connected` reflects Lavalink's view of the voice gateway
    (from the per-player state endpoint's `state.connected` field).
    The primary wedge signal: when this flips to False mid-track,
    we know frames aren't reaching Discord even if `position` keeps
    advancing.

    `frame_deficit` is the AGGREGATE frame deficit from /v4/stats
    (frames Lavalink couldn't push to its UDP socket). Cumulative
    counter; we look at growth between samples, not absolute value.
    Fallback signal for the case where `voice_connected` lags or
    misreports.
    """

    track_identifier: str | None
    position_ms: int
    voice_connected: bool
    frame_deficit: int  # cumulative, monotonic
    is_playing: bool
    is_paused: bool
    sampled_at: float  # monotonic seconds (time.monotonic())


def should_recover(
    curr: VoiceHealthSnapshot,
    prev: VoiceHealthSnapshot | None,
    consecutive_wedge_samples: int,
    last_recovery_at: float | None,
    recovery_attempts_for_track: int,
    now: float,
) -> RecoveryAction:
    """Decide whether the current heartbeat sample warrants action.

    PURE function -- no I/O, no side effects, fully deterministic on
    its inputs. Every branch is tested in tests/unit/test_voice_health.py.

    Returns:
      NONE -- healthy, paused, just started, recently recovered, or not
              enough confirming samples yet.
      RECOVER -- wedge confirmed AND we haven't exhausted the per-track
                 retry budget. Cog reconnects voice + replays at saved
                 position.
      GIVE_UP_AND_SKIP -- wedge confirmed AND we already used the
                          per-track retry. Cog announces + skips track.

    Ordering of checks matters; falsest-positive guards run first so
    paused / between-tracks samples never propagate to the wedge
    branches. Followed by the throttle so a recovery in progress
    doesn't trigger a second recovery while the voice WS is mid-
    handshake.
    """
    # No track loaded -> no wedge possible.
    if curr.track_identifier is None:
        return RecoveryAction.NONE

    # Paused / explicitly not playing -> no wedge.
    if not curr.is_playing or curr.is_paused:
        return RecoveryAction.NONE

    # First sample for this track (or first sample ever) -> need a
    # baseline before we can detect a delta-based wedge.
    if prev is None or prev.track_identifier != curr.track_identifier:
        return RecoveryAction.NONE

    # Within the cooldown after a previous recovery? Suppress so the
    # heartbeat doesn't re-fire while voice is still re-handshaking.
    # The throttle is also belt-and-suspenders against pathological
    # cases where state.connected briefly flickers post-reconnect.
    if last_recovery_at is not None and (now - last_recovery_at) < _RECOVERY_THROTTLE_SECONDS:
        return RecoveryAction.NONE

    # Wedge signal: either Lavalink reports the voice WS as
    # disconnected, OR the aggregate frame deficit jumped by enough
    # that we know audio frames aren't reaching Discord. Either alone
    # is sufficient; we OR them so a stuck Lavalink-side state field
    # doesn't hide a real frame-drop event and vice versa.
    voice_dead = not curr.voice_connected
    deficit_grew_by = curr.frame_deficit - prev.frame_deficit
    deficit_spike = deficit_grew_by >= _DEFICIT_GROWTH_THRESHOLD
    sample_indicates_wedge = voice_dead or deficit_spike

    if not sample_indicates_wedge:
        return RecoveryAction.NONE

    # Confirmed-wedge sample, but we require N in a row to fire so a
    # single jittery read doesn't cause churn. The caller increments
    # `consecutive_wedge_samples` on each NONE-but-still-suspicious
    # return; here we just look at whether we've crossed the bar
    # INCLUDING this sample. Caller passes the post-increment value.
    if consecutive_wedge_samples < _WEDGE_CONFIRMATION_SAMPLES:
        return RecoveryAction.NONE

    # We're firing. Choose between retry and give-up based on whether
    # this track has already had a recovery attempt.
    if recovery_attempts_for_track >= _MAX_RECOVERY_ATTEMPTS_PER_TRACK:
        return RecoveryAction.GIVE_UP_AND_SKIP

    return RecoveryAction.RECOVER


# ---------------------------------------------------------------------------
# Recovery I/O helpers (used by the cog's event handlers + heartbeat)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PlayerStateProbe:
    """Slim subset of Lavalink's /v4/sessions/{sid}/players/{gid}
    response that we actually care about. Fetched by `fetch_player_state`.
    """

    connected: bool
    position_ms: int


async def fetch_player_state(
    node_uri: str,
    password: str,
    session_id: str,
    guild_id: int,
) -> _PlayerStateProbe | None:
    """GET Lavalink's per-player state. Returns None on any HTTP error
    so the heartbeat caller can treat transient blips as "no signal"
    rather than as a wedge.

    URL shape: {node_uri}/v4/sessions/{session_id}/players/{guild_id}
    Auth: Authorization: {password}

    Lavalink response (only fields we read):
      {
        "state": {
          "connected": bool,
          "position": int (ms),
          ...
        },
        ...
      }
    """
    url = f"{node_uri.rstrip('/')}/v4/sessions/{session_id}/players/{guild_id}"
    headers = {"Authorization": password}
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_CHECK_TIMEOUT_SECONDS) as client_http:
            resp = await client_http.get(url, headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("fetch_player_state: HTTP error", url=url, error=repr(exc))
        return None
    if resp.status_code != 200:
        # 404 is the common case here -- session/player not yet
        # registered when the heartbeat fires racey close to track
        # start. Logging at debug to avoid spam.
        logger.debug(
            "fetch_player_state: non-200", url=url, status=resp.status_code
        )
        return None
    try:
        body = resp.json()
        state = body.get("state") or {}
        return _PlayerStateProbe(
            connected=bool(state.get("connected", False)),
            position_ms=int(state.get("position", 0)),
        )
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning("fetch_player_state: parse error", error=repr(exc))
        return None


async def fetch_aggregate_frame_deficit(node_uri: str, password: str) -> int | None:
    """GET Lavalink's /v4/stats and return frameStats.deficit.

    `deficit` is a cumulative counter of frames Lavalink failed to
    push to its UDP socket. Rises monotonically; the heartbeat
    compares samples to detect growth (a real-time signal that voice
    frames aren't reaching Discord).

    Returns None on any HTTP error. Per-node aggregate, NOT per-player
    -- a known limitation when the bot is playing in multiple guilds
    simultaneously (rare for a hobby bot; documented in PRD decisions).
    """
    url = f"{node_uri.rstrip('/')}/v4/stats"
    headers = {"Authorization": password}
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_CHECK_TIMEOUT_SECONDS) as client_http:
            resp = await client_http.get(url, headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("fetch_aggregate_frame_deficit: HTTP error", url=url, error=repr(exc))
        return None
    if resp.status_code != 200:
        return None
    try:
        body = resp.json()
        frame_stats = body.get("frameStats") or {}
        # frameStats is null on Lavalink nodes with no active players.
        # Return 0 so the heartbeat treats "no audio in flight" as
        # "no deficit" instead of "no signal".
        if not frame_stats:
            return 0
        return int(frame_stats.get("deficit", 0))
    except (ValueError, TypeError) as exc:
        logger.warning("fetch_aggregate_frame_deficit: parse error", error=repr(exc))
        return None


async def reconnect_player_at_position(
    player: wavelink.Player,
    voice_channel: discord.VoiceChannel | discord.StageChannel,
) -> bool:
    """Tear down + re-establish the voice gateway session for a player,
    then resume playback at the previously-saved position.

    Returns True if the reconnect appeared to succeed (playback
    resumed), False on any failure -- caller can fall back to skip.

    Why not `player.move_to(channel)` or `player.connect(...)` on the
    existing player? Both keep the same voice gateway session under
    the hood, which is exactly the thing that's wedged. We need
    Discord to issue a fresh voice token, which requires a full
    disconnect + reconnect cycle.
    """
    if player.current is None:
        # Nothing to resume. Treat as no-op success; the cog's
        # caller will skip the (empty) recovery path.
        logger.info("reconnect_player_at_position: no current track; nothing to resume")
        return True

    saved_track = player.current
    saved_position = player.position

    logger.info(
        "reconnect_player_at_position: starting",
        track=saved_track.title,
        position_ms=saved_position,
    )

    try:
        # disconnect() drops the voice gateway session AND removes the
        # player from the guild's voice_clients dict. We have to
        # re-establish via voice_channel.connect(cls=Player) below.
        await player.disconnect()
    except Exception as exc:  # noqa: BLE001 -- best-effort teardown; we always reconnect after
        logger.warning("reconnect: disconnect raised, continuing", error=repr(exc))

    # Small breathing room so Discord registers the leave before we
    # send the new IDENTIFY. Without this, the new connect occasionally
    # races and lands a "already connected" rejection.
    await asyncio.sleep(0.5)

    try:
        new_player: wavelink.Player = await voice_channel.connect(cls=wavelink.Player)
    except Exception as exc:  # noqa: BLE001 -- reconnect failure is the bug we're handling
        logger.error("reconnect: voice_channel.connect failed", error=repr(exc))
        return False

    new_player.autoplay = wavelink.AutoPlayMode.partial

    try:
        await new_player.play(saved_track, start=saved_position)
    except Exception as exc:  # noqa: BLE001 -- caller falls back to skip
        logger.error("reconnect: play(saved_track) failed", error=repr(exc))
        return False

    logger.info(
        "reconnect_player_at_position: resumed",
        track=saved_track.title,
        resumed_at_ms=saved_position,
    )
    return True


# Re-exported so the cog can use these for its own asyncio.wait_for and
# wavelink event hooks without importing wavelink directly.
TrackEndEventPayload = wavelink.TrackEndEventPayload
NodeReadyEventPayload = wavelink.NodeReadyEventPayload


__all__ = [
    "PLAYLIST_TRACK_CAP",
    "NodeReadyEventPayload",
    "PlayResult",
    "RecoveryAction",
    "TrackEndEventPayload",
    "TrackInfo",
    "VoiceHealthSnapshot",
    "connect_node",
    "fetch_aggregate_frame_deficit",
    "fetch_player_state",
    "format_duration",
    "now_playing",
    "pause",
    "play",
    "queue_snapshot",
    "reconnect_player_at_position",
    "resume",
    "set_loop",
    "set_volume",
    "should_recover",
    "shuffle",
    "skip",
    "stop_and_disconnect",
]
