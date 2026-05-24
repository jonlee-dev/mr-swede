"""Voice-gateway-recovery subsystem for the music cog.

Background. The 2026-05-12 incident: mid-session, Discord's voice
server reset the UDP connection (Koe logged `recvAddress(..) failed
with error(-104): Connection reset by peer`). Lavalink kept advancing
the player's internal position as if audio were still flowing, so the
bot had no way to detect the wedge from `player.position` alone.
Users heard silence for ~3min before manually running `/music stop`.

This module implements bot-side recovery. Two signals feed the
decision function below:

  1. Event-driven (`on_wavelink_websocket_closed` in cogs/music.py)
     catches CLEAN voice gateway closes (codes 4006/4014/4015 = server
     migration, transport reset that took the WS down with it). This
     is the common case but NOT guaranteed to fire for every wedge
     shape we've seen.

  2. Heartbeat-driven (2s cadence in cogs/music.py) polls Lavalink's
     per-player state via `fetch_player_state` and aggregate frame
     deficit via `fetch_aggregate_frame_deficit`, feeds successive
     `VoiceHealthSnapshot`s into `should_recover`. Catches wedges
     that don't surface as Wavelink events.

`should_recover` is a pure function over snapshot history and a
per-track attempt counter; unit-tested in tests/unit/test_voice_health.py.
The cog wraps it with side effects (reconnect / skip / post message).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum

import discord
import httpx
import wavelink

from src.config.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tuneables
# ---------------------------------------------------------------------------

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
DEFICIT_GROWTH_THRESHOLD = 25

# HTTP timeout for Lavalink probes. Short -- localhost should respond
# in single-digit ms; a 3s ceiling catches a wedged Lavalink without
# blocking the heartbeat for long.
_HTTP_TIMEOUT_SECONDS = 3.0


# ---------------------------------------------------------------------------
# Pure decision types
# ---------------------------------------------------------------------------


class RecoveryAction(str, Enum):
    """Outcome of `should_recover()`. Pure-data return so the cog can
    dispatch (post message + reconnect vs post message + skip) without
    `should_recover` needing to know about Discord channels.
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


@dataclass
class GuildRecoveryState:
    """Per-guild bookkeeping. Mutated by the heartbeat tick + the
    event-handler dispatch on every wedge signal.

    Lives in MusicCog._recovery_state keyed by guild.id. Cleared
    whenever the player disconnects (cog drops the entry).
    """

    last_snapshot: VoiceHealthSnapshot | None = None
    consecutive_wedge_samples: int = 0
    last_recovery_at: float | None = None
    # Reset when track_identifier changes. The recovery budget is
    # PER-TRACK: a wedged track gets one retry, then skipped.
    recovery_attempts_for_track: int = 0
    # Identifier of the track the attempts counter is bound to. When
    # we see a new identifier we zero `recovery_attempts_for_track`
    # before evaluating.
    attempts_bound_to_track: str | None = None


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

    Ordering of checks matters; false-positive guards run first so
    paused / between-tracks samples never propagate to the wedge
    branches. Throttle runs before the wedge signal so a recovery in
    progress doesn't trigger a second recovery while the voice WS is
    mid-handshake.
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
    if last_recovery_at is not None and (now - last_recovery_at) < _RECOVERY_THROTTLE_SECONDS:
        return RecoveryAction.NONE

    # Wedge signal: either Lavalink reports the voice WS as
    # disconnected, OR the aggregate frame deficit jumped by enough
    # that we know audio frames aren't reaching Discord. OR'd so a
    # stuck Lavalink-side state field doesn't hide a real frame-drop
    # event and vice versa.
    voice_dead = not curr.voice_connected
    deficit_grew_by = curr.frame_deficit - prev.frame_deficit
    deficit_spike = deficit_grew_by >= DEFICIT_GROWTH_THRESHOLD
    if not (voice_dead or deficit_spike):
        return RecoveryAction.NONE

    # Wedge confirmed by THIS sample, but we require N in a row before
    # firing so single-read jitter doesn't churn. Caller passes the
    # post-increment value.
    if consecutive_wedge_samples < _WEDGE_CONFIRMATION_SAMPLES:
        return RecoveryAction.NONE

    # Fire. Retry vs give-up depends on the per-track budget.
    if recovery_attempts_for_track >= _MAX_RECOVERY_ATTEMPTS_PER_TRACK:
        return RecoveryAction.GIVE_UP_AND_SKIP
    return RecoveryAction.RECOVER


def synthesize_event_snapshot(track_identifier: str | None, now: float) -> VoiceHealthSnapshot:
    """Build a synthetic `VoiceHealthSnapshot` representing a confirmed
    wedge, for callers (the on_wavelink_websocket_closed handler) that
    already KNOW the voice WS just closed.

    Pairs with `event_driven_action()` to feed the event-handler path
    through the same `should_recover` machinery the heartbeat uses --
    one decision function, one set of tests, no duplicated budget-vs-
    skip branching in the cog.
    """
    return VoiceHealthSnapshot(
        track_identifier=track_identifier,
        position_ms=0,  # unused by should_recover; event path doesn't have it
        voice_connected=False,
        frame_deficit=0,
        is_playing=True,
        is_paused=False,
        sampled_at=now,
    )


def event_driven_action(
    state: GuildRecoveryState,
    track_identifier: str | None,
    now: float,
) -> RecoveryAction:
    """Decide what to do when Wavelink reports a recoverable voice WS
    close. Wraps `should_recover` with the event-path conventions:

      - A close event IS a confirmed wedge (2 consecutive samples).
      - We supply the same per-track budget + throttle the heartbeat
        does, so a heartbeat-triggered recovery 10s ago STILL throttles
        a close-event-triggered recovery here.

    Mutates `state` only to the extent of binding the attempts counter
    to the current track. Bumping the counter / setting last_recovery_at
    happens in the dispatcher AFTER the decision lands.
    """
    if state.attempts_bound_to_track != track_identifier:
        state.attempts_bound_to_track = track_identifier
        state.recovery_attempts_for_track = 0

    snapshot = synthesize_event_snapshot(track_identifier, now)
    # Use prev = a same-track baseline so should_recover doesn't bail
    # on the "first sample of this track" branch. frame_deficit equal
    # means no spike; voice_connected=False carries the wedge signal.
    baseline = VoiceHealthSnapshot(
        track_identifier=track_identifier,
        position_ms=0,
        voice_connected=True,
        frame_deficit=0,
        is_playing=True,
        is_paused=False,
        sampled_at=now,
    )
    return should_recover(
        curr=snapshot,
        prev=baseline,
        consecutive_wedge_samples=_WEDGE_CONFIRMATION_SAMPLES,
        last_recovery_at=state.last_recovery_at,
        recovery_attempts_for_track=state.recovery_attempts_for_track,
        now=now,
    )


# ---------------------------------------------------------------------------
# Recovery I/O helpers (HTTP probes + reconnect dance)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlayerStateProbe:
    """Slim subset of Lavalink's /v4/sessions/{sid}/players/{gid}
    response that we actually care about.
    """

    connected: bool
    position_ms: int


async def fetch_player_state(
    node_uri: str,
    password: str,
    session_id: str,
    guild_id: int,
) -> PlayerStateProbe | None:
    """GET Lavalink's per-player state. Returns None on any HTTP error
    so the heartbeat caller can treat transient blips as "no signal"
    rather than as a wedge.
    """
    url = f"{node_uri.rstrip('/')}/v4/sessions/{session_id}/players/{guild_id}"
    headers = {"Authorization": password}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("fetch_player_state: HTTP error", url=url, error=repr(exc))
        return None
    if resp.status_code != 200:
        # 404 is the common case: session/player not yet registered
        # when the heartbeat fires racey close to track start.
        logger.debug("fetch_player_state: non-200", url=url, status=resp.status_code)
        return None
    try:
        body = resp.json()
        state = body.get("state") or {}
        return PlayerStateProbe(
            connected=bool(state.get("connected", False)),
            position_ms=int(state.get("position", 0)),
        )
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning("fetch_player_state: parse error", error=repr(exc))
        return None


async def fetch_aggregate_frame_deficit(node_uri: str, password: str) -> int | None:
    """GET Lavalink's /v4/stats and return frameStats.deficit.

    `deficit` is a cumulative counter of frames Lavalink failed to
    push to its UDP socket. Rises monotonically; the heartbeat compares
    samples to detect growth. Per-node aggregate (not per-player) --
    known limitation for the rare multi-guild-concurrent case.
    """
    url = f"{node_uri.rstrip('/')}/v4/stats"
    headers = {"Authorization": password}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("fetch_aggregate_frame_deficit: HTTP error", url=url, error=repr(exc))
        return None
    if resp.status_code != 200:
        return None
    try:
        body = resp.json()
        frame_stats = body.get("frameStats") or {}
        # frameStats is null when no players are active. Treat as "no
        # deficit" instead of "no signal" so the heartbeat doesn't
        # discard healthy ticks during quiet periods.
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

    Returns True if the reconnect appeared to succeed, False on any
    failure -- caller can fall back to skip.

    Why not `player.move_to(channel)` or `player.connect(...)` on the
    existing player? Both keep the same voice gateway session under
    the hood, which is exactly the thing that's wedged. We need
    Discord to issue a fresh voice token, which requires a full
    disconnect + reconnect cycle.
    """
    if player.current is None:
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
        await player.disconnect()
    except Exception as exc:  # noqa: BLE001 -- best-effort teardown
        logger.warning("reconnect: disconnect raised, continuing", error=repr(exc))

    # Breathing room so Discord registers the leave before the new
    # IDENTIFY. Without this, the connect occasionally races into an
    # "already connected" rejection.
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


__all__ = [
    "DEFICIT_GROWTH_THRESHOLD",
    "GuildRecoveryState",
    "PlayerStateProbe",
    "RecoveryAction",
    "VoiceHealthSnapshot",
    "event_driven_action",
    "fetch_aggregate_frame_deficit",
    "fetch_player_state",
    "reconnect_player_at_position",
    "should_recover",
    "synthesize_event_snapshot",
]
