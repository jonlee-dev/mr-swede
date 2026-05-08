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

# How long connect_node waits for the Wavelink Node to reach CONNECTED
# state. This was 30s, but on a 2026-05-07 cold-start incident every
# /music play attempt timed out at 30s while Lavalink was still
# finishing its boot (BepInExPack/lavasrc plugin downloads from
# Thunderstore on first run + JVM startup). The cog's user-facing
# "wait ~90 seconds and try again" message is the actual cold-start
# expectation, so the connect timeout should match it. 90s gives the
# JVM-on-GCE plenty of headroom; longer would just delay error
# reporting on legitimately broken servers.
_CONNECT_TIMEOUT_SECONDS = 90.0

_CONNECT_POLL_INTERVAL_SECONDS = 0.5
_HEALTH_CHECK_TIMEOUT_SECONDS = 3.0


async def _node_is_live(uri: str, password: str) -> bool:
    """Hit Lavalink's `/v4/info` to verify the node is actually reachable.

    Wavelink's `NodeStatus.CONNECTED` only reflects the WebSocket
    handshake state; it doesn't catch the case where the underlying
    Lavalink VM was stopped + started behind us (idle-watcher cycle,
    new public IP, fresh session_id) but our cached Node still claims
    to be CONNECTED. The bot would then pass this stale node to
    `Playable.search()` and get a 404 "Session not found".

    A 200 OK from `/v4/info` confirms HTTP reachability AND password
    validity; that's a strong-enough signal that the node is usable.
    Any error response (404/timeout/connect-refused) means we should
    treat the cached node as stale and reconnect from scratch.
    """
    info_url = f"{uri.rstrip('/')}/v4/info"
    headers = {"Authorization": password}
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_CHECK_TIMEOUT_SECONDS) as client_http:
            resp = await client_http.get(info_url, headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("Lavalink /v4/info probe failed", uri=info_url, error=repr(exc))
        return False
    if resp.status_code != 200:
        logger.warning(
            "Lavalink /v4/info returned non-200, treating node as stale",
            uri=info_url,
            status=resp.status_code,
        )
        return False
    return True


async def _drop_stale_node(node: wavelink.Node) -> None:
    """Close + evict a node so the next connect_node call gets a clean slate.

    Wavelink doesn't expose a public "remove node" API; the safe
    sequence is `node.close()` (which sets status to DISCONNECTED and
    closes the WS) followed by popping it from `Pool.nodes`. After
    this, `Pool.connect(...)` accepts a fresh Node with the same
    identifier without raising 'NodeAlreadyExists'.
    """
    try:
        await node.close()
    except Exception as exc:  # noqa: BLE001 -- best-effort cleanup; we don't care why close failed
        logger.warning("node.close() raised during stale-node eviction", error=repr(exc))
    wavelink.Pool.nodes.pop(_NODE_IDENTIFIER, None)


async def connect_node(client: discord.Client, host: str, port: int, password: str) -> None:
    """Open the WebSocket to Lavalink and wait until the node is CONNECTED.

    Behaviors of wavelink 3.x covered here:

      1. `Pool.connect()` requires a `client=` kwarg. Without it, the
         pool can't subscribe to discord.py's voice gateway events
         and the node never finishes its handshake.

      2. `Pool.connect()` is fire-and-forget -- it queues the
         WebSocket connection and returns immediately, BEFORE the
         node reaches CONNECTED state. Calling `Playable.search()`
         right after raises "No nodes are currently assigned to the
         wavelink.Pool in a CONNECTED state". We poll node.status
         here until ready (or timeout).

      3. Idempotent fast-path: if a node with the same identifier is
         in the pool, CONNECTED, pointing at the same URI, AND a
         /v4/info probe succeeds, we return immediately without
         re-creating it.

      4. Stale-node eviction: any other state of the existing-node
         entry forces an evict-and-reconnect:

           - CONNECTED but URI changed (idle-watcher cycled the VM,
             new public IP) -> _v4/info_ probe catches this
           - CONNECTED but /v4/info fails (Lavalink restarted; cached
             session_id is invalid) -> probe also catches this
           - DISCONNECTED (Wavelink lost the WS when Lavalink VM was
             stopped by the watcher; the entry is still in
             Pool.nodes but useless) -> THIS BRANCH FIXES THE
             2026-05-04 bug where /music play after a watcher stop
             logged "Unable to connect ... as you already have a
             node with identifier" and silently failed
           - CONNECTING / any other transitional -> evict to be safe;
             reconnect is cheap, leaving a half-dead entry isn't

         Without these, an idle-watcher-induced VM cycle would
         require a manual bot bounce.
    """
    uri = f"http://{host}:{port}"

    existing = wavelink.Pool.nodes.get(_NODE_IDENTIFIER)
    if existing is not None:
        is_connected = existing.status is wavelink.NodeStatus.CONNECTED
        existing_uri = getattr(existing, "uri", None)
        if is_connected and existing_uri == uri and await _node_is_live(uri, password):
            # Fast-path: same URI, healthy probe, definitely usable.
            logger.debug("Lavalink node already connected and healthy", uri=uri)
            return
        # Any other state (DISCONNECTED, CONNECTED-but-stale, CONNECTING,
        # ...) means the cached entry can't be reused. Evict so the
        # Pool.connect below doesn't trip on the duplicate identifier.
        logger.info(
            "Cached Lavalink node not usable; evicting and reconnecting",
            cached_status=str(existing.status),
            cached_uri=existing_uri,
            new_uri=uri,
        )
        await _drop_stale_node(existing)

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


# Re-exported so the cog can use these for its own asyncio.wait_for and
# wavelink event hooks without importing wavelink directly.
TrackEndEventPayload = wavelink.TrackEndEventPayload
NodeReadyEventPayload = wavelink.NodeReadyEventPayload


__all__ = [
    "PLAYLIST_TRACK_CAP",
    "PlayResult",
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
