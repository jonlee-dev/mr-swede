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
from typing import Any

import discord
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
# 1000 (2026-05-26 bump from 100) to cover large user playlists while
# still preventing a runaway YouTube auto-mix / radio from filling the
# queue unboundedly. If a playlist exceeds this, we keep the first
# PLAYLIST_TRACK_CAP tracks and surface "truncated to N/M" in the embed.
#
# Memory: each queued entry is a wavelink.Playable holding metadata only
# (~2-3KB); 1000 entries is a few MB per guild. Mirror resolution for
# Spotify tracks stays LAZY (happens at play-time, not enqueue), so a
# 1000-track load is just metadata paging, not 1000 YouTube searches.
# MUST stay in sync with the Lavalink-side limits in
# server/lavalink/application.yml (youtubePlaylistLoadLimit +
# lavasrc.spotify.{playlistLoadLimit,albumLoadLimit}).
PLAYLIST_TRACK_CAP = 1000


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


async def clear_queue(guild: discord.Guild) -> int:
    """Clear the UPCOMING queue without touching the current track or
    leaving voice. Returns the number of tracks removed (0 if the queue
    was already empty or we're not connected).

    Contrast with stop_and_disconnect (stops the current track + clears
    + disconnects) and skip (advances past the current track). This is
    the "I queued a bunch of junk, wipe what's next but keep this song"
    operation.

    We also clear `auto_queue` defensively: with AutoPlayMode.partial it
    stays empty (we don't fetch recommendations), but clearing it costs
    nothing and guards against a future autoplay-mode change leaving
    stale recommendations behind.
    """
    player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
    if player is None:
        return 0
    count = len(player.queue)
    player.queue.clear()
    auto_queue = getattr(player, "auto_queue", None)
    if auto_queue is not None:
        auto_queue.clear()
    return count


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


def queue_length(guild: discord.Guild) -> int:
    """Total number of tracks waiting in the upcoming queue (excludes the
    currently-playing track). Lets the cog show "showing 10 of N" so a
    1000-track queue isn't silently misrepresented as just the 10 we
    list in the embed.
    """
    player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
    if player is None:
        return 0
    return len(player.queue)


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


# Re-exported so the cog can use these for its own asyncio.wait_for and
# wavelink event hooks without importing wavelink directly.
TrackEndEventPayload = wavelink.TrackEndEventPayload
NodeReadyEventPayload = wavelink.NodeReadyEventPayload


__all__ = [
    "PLAYLIST_TRACK_CAP",
    "NodeReadyEventPayload",
    "PlayResult",
    "TrackEndEventPayload",
    "TrackInfo",
    "clear_queue",
    "connect_node",
    "format_duration",
    "now_playing",
    "pause",
    "play",
    "queue_length",
    "queue_snapshot",
    "resume",
    "set_loop",
    "set_volume",
    "shuffle",
    "skip",
    "stop_and_disconnect",
]
