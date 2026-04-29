"""Music service: thin wrapper over wavelink for Lavalink-backed audio.

Owns the Lavalink node lifecycle (connect on bot ready, reconnect on
WebSocket close) and exposes a small set of operations the cog uses.
The cog never imports `wavelink` directly -- everything routes through
this module so:

  - The wavelink dependency stays pinned in one place
  - Tests of the cog can mock this module without library knowledge
  - Future swap to a different Lavalink client (Pomice, Mafic) is
    contained to this file

Per the PRD's TDD answer: this module is NOT unit-tested. Wavelink's
wire protocol against a real Lavalink is what we actually care about,
and we exercise it via the live integration probe we already validated
in Phase 1.
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


def _to_track_info(track: wavelink.Playable, requester_id: int | None = None) -> TrackInfo:
    return TrackInfo(
        title=track.title,
        author=track.author or "unknown",
        duration_ms=track.length,
        uri=getattr(track, "uri", None),
        requester_id=requester_id,
    )


_NODE_IDENTIFIER = "mr-swede-main"
_CONNECT_TIMEOUT_SECONDS = 30.0
_CONNECT_POLL_INTERVAL_SECONDS = 0.5


async def connect_node(client: discord.Client, host: str, port: int, password: str) -> None:
    """Open the WebSocket to Lavalink and wait until the node is CONNECTED.

    Two non-obvious behaviors of wavelink 3.x covered here:

      1. `Pool.connect()` requires a `client=` kwarg. Without it, the
         pool can't subscribe to discord.py's voice gateway events
         and the node never finishes its handshake.

      2. `Pool.connect()` is fire-and-forget -- it queues the
         WebSocket connection and returns immediately, BEFORE the
         node reaches CONNECTED state. Calling `Playable.search()`
         right after raises "No nodes are currently assigned to the
         wavelink.Pool in a CONNECTED state". We poll node.status
         here until ready (or timeout).

      3. Idempotent: if a node with the same identifier is already
         in the pool and CONNECTED, we return immediately without
         re-creating it.
    """
    uri = f"http://{host}:{port}"

    # Idempotent fast path: if the pool already has our node connected,
    # nothing to do.
    existing = wavelink.Pool.nodes.get(_NODE_IDENTIFIER)
    if existing is not None and existing.status is wavelink.NodeStatus.CONNECTED:
        logger.debug("Lavalink node already connected", uri=uri)
        return

    logger.info("Connecting to Lavalink node", uri=uri)
    node = wavelink.Node(uri=uri, password=password, identifier=_NODE_IDENTIFIER)
    await wavelink.Pool.connect(client=client, nodes=[node])

    # Poll until CONNECTED. Bail if it doesn't happen within
    # CONNECT_TIMEOUT_SECONDS so the cog can surface a useful error
    # instead of hanging forever.
    deadline = asyncio.get_event_loop().time() + _CONNECT_TIMEOUT_SECONDS
    while asyncio.get_event_loop().time() < deadline:
        # Re-fetch from the pool because Pool.connect may swap node
        # objects internally during reconnection paths.
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
) -> tuple[TrackInfo | None, int]:
    """Resolve `query`, enqueue it on the guild's player, and start
    playback if nothing is playing. Returns (now_playing_or_queued,
    queue_position). Position is 0 when the track starts playing
    immediately, otherwise its 1-based position in the queue.

    Joins `voice_channel` if the player isn't already connected.
    Raises if the query resolves to nothing.
    """
    guild = voice_channel.guild
    player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
    if player is None:
        player = await voice_channel.connect(cls=wavelink.Player)
    elif player.channel != voice_channel:
        await player.move_to(voice_channel)

    # wavelink.Playable.search defaults to `ytmsearch:` (YouTube Music)
    # when no prefix is present. Don't prepend `ytsearch:` ourselves --
    # wavelink would treat the whole thing as a literal search string
    # ("ytmsearch:ytsearch:hi") and resolve to nonsense. URLs pass
    # through unmodified.
    tracks: Any = await wavelink.Playable.search(query)
    if not tracks:
        return (None, 0)

    track = tracks[0] if not isinstance(tracks, wavelink.Playlist) else tracks.tracks[0]

    if not player.playing and player.queue.is_empty:
        await player.play(track)
        return (_to_track_info(track, requester_id), 0)

    player.queue.put(track)
    return (_to_track_info(track, requester_id), len(player.queue))


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


# Re-exported so the cog can use these for its own asyncio.wait_for and
# wavelink event hooks without importing wavelink directly.
TrackEndEventPayload = wavelink.TrackEndEventPayload
NodeReadyEventPayload = wavelink.NodeReadyEventPayload


__all__ = [
    "TrackInfo",
    "TrackEndEventPayload",
    "NodeReadyEventPayload",
    "connect_node",
    "play",
    "skip",
    "pause",
    "resume",
    "stop_and_disconnect",
    "now_playing",
    "queue_snapshot",
    "set_volume",
    "shuffle",
    "set_loop",
    "format_duration",
]


# `asyncio` is imported to keep the type stub clean; if you remove all
# async references this guards against a "imported but unused" hit.
_ = asyncio
