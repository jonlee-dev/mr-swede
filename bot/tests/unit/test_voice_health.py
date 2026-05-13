"""Unit tests for the voice-gateway-recovery decision logic in
src.services.music (`should_recover`).

Pure-function tests, mirroring the `/livez` `_LivenessSnapshot` pattern
in tests/unit/test_http.py. We never construct a real wavelink.Player
or hit a real Lavalink -- the decision function operates on
VoiceHealthSnapshot dataclasses, so we just build them by hand.

Why these tests matter. The 2026-05-12 incident was a silent audio
drop -- Discord's voice server reset the UDP connection mid-track and
Lavalink kept advancing position as if nothing happened. The fix's
correctness lives entirely in this branch logic: one missed branch
and a real wedge sails through, or a benign sample triggers a false
recovery. Each `RecoveryAction` outcome must be reachable by exactly
the inputs documented in the should_recover docstring.
"""

from __future__ import annotations

from src.services.music import (
    RecoveryAction,
    VoiceHealthSnapshot,
    should_recover,
)


def _snap(**overrides: object) -> VoiceHealthSnapshot:
    """Build a baseline VoiceHealthSnapshot that represents a healthy,
    actively-playing track. Tests override one or two fields at a
    time to drive specific branches.
    """
    base = {
        "track_identifier": "track-A",
        "position_ms": 30_000,
        "voice_connected": True,
        "frame_deficit": 0,
        "is_playing": True,
        "is_paused": False,
        "sampled_at": 100.0,
    }
    base.update(overrides)
    return VoiceHealthSnapshot(**base)  # type: ignore[arg-type]


class TestShouldRecoverHealthyAndBaseline:
    """Cases where we expect RecoveryAction.NONE because nothing is
    actually wrong, OR because we don't have enough signal yet."""

    def test_healthy_steady_state_returns_none(self) -> None:
        # Two healthy samples in a row, voice connected, no deficit.
        prev = _snap(sampled_at=98.0)
        curr = _snap(sampled_at=100.0)
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=0,
            last_recovery_at=None,
            recovery_attempts_for_track=0,
            now=100.0,
        )
        assert action is RecoveryAction.NONE

    def test_no_prev_snapshot_returns_none(self) -> None:
        # Bot just started; we don't have a baseline yet, so even a
        # disconnected-looking snapshot shouldn't trigger.
        curr = _snap(voice_connected=False)
        action = should_recover(
            curr=curr,
            prev=None,
            consecutive_wedge_samples=1,
            last_recovery_at=None,
            recovery_attempts_for_track=0,
            now=100.0,
        )
        assert action is RecoveryAction.NONE

    def test_no_track_loaded_returns_none(self) -> None:
        # Between tracks: track_identifier is None. Even if everything
        # else looks bad, there's no wedge to act on.
        curr = _snap(track_identifier=None, voice_connected=False, is_playing=False)
        prev = _snap(track_identifier=None, sampled_at=98.0)
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=2,
            last_recovery_at=None,
            recovery_attempts_for_track=0,
            now=100.0,
        )
        assert action is RecoveryAction.NONE

    def test_paused_returns_none(self) -> None:
        # User paused. We shouldn't act on a wedge signal while
        # paused -- the track isn't supposed to be advancing.
        curr = _snap(is_paused=True, voice_connected=False)
        prev = _snap(is_paused=True, sampled_at=98.0)
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=2,
            last_recovery_at=None,
            recovery_attempts_for_track=0,
            now=100.0,
        )
        assert action is RecoveryAction.NONE

    def test_not_playing_returns_none(self) -> None:
        # Player is "not playing" -- queue ran out, between tracks,
        # or just connected. Same logic as paused.
        curr = _snap(is_playing=False, voice_connected=False)
        prev = _snap(is_playing=False, sampled_at=98.0)
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=2,
            last_recovery_at=None,
            recovery_attempts_for_track=0,
            now=100.0,
        )
        assert action is RecoveryAction.NONE

    def test_track_change_resets_state(self) -> None:
        # We just transitioned from track-A to track-B. Even if the
        # NEW sample looks bad, we don't act on the FIRST sample of
        # a new track -- need a baseline to compare against.
        prev = _snap(track_identifier="track-A", sampled_at=98.0)
        curr = _snap(track_identifier="track-B", voice_connected=False)
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=1,
            last_recovery_at=None,
            recovery_attempts_for_track=0,
            now=100.0,
        )
        assert action is RecoveryAction.NONE


class TestShouldRecoverWedgeConfirmation:
    """The wedge-confirmation samples requirement. A single bad
    sample shouldn't trigger -- need 2 in a row."""

    def test_single_bad_sample_returns_none(self) -> None:
        # voice_connected=False on the current sample but
        # consecutive_wedge_samples=1 (caller has just incremented
        # to 1 because this is the FIRST bad sample). We require >=2.
        prev = _snap(sampled_at=98.0)
        curr = _snap(voice_connected=False)
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=1,
            last_recovery_at=None,
            recovery_attempts_for_track=0,
            now=100.0,
        )
        assert action is RecoveryAction.NONE

    def test_two_consecutive_bad_samples_triggers_recover(self) -> None:
        # Second bad sample in a row, no prior recovery attempts on
        # this track -> reconnect at saved position.
        prev = _snap(voice_connected=False, sampled_at=98.0)
        curr = _snap(voice_connected=False)
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=2,
            last_recovery_at=None,
            recovery_attempts_for_track=0,
            now=100.0,
        )
        assert action is RecoveryAction.RECOVER


class TestShouldRecoverFrameDeficitSignal:
    """The frame-deficit secondary signal: voice_connected might lag
    or misreport, but if the aggregate frame deficit jumps, we know
    Lavalink couldn't deliver audio frames."""

    def test_small_deficit_growth_is_not_a_wedge(self) -> None:
        # < 25 frame growth between samples = single-frame UDP jitter,
        # ignore. We're at 50fps so 25 = half a second of dropped audio.
        prev = _snap(frame_deficit=100, sampled_at=98.0)
        curr = _snap(frame_deficit=110)  # +10, below threshold
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=1,  # increment would be skipped
            last_recovery_at=None,
            recovery_attempts_for_track=0,
            now=100.0,
        )
        assert action is RecoveryAction.NONE

    def test_large_deficit_growth_counts_as_wedge_signal(self) -> None:
        # +50 frames between samples = ~1s of dropped audio. Combined
        # with consecutive_wedge_samples=2 from the caller (deficit
        # has been growing for two samples now), we recover.
        prev = _snap(frame_deficit=100, sampled_at=98.0)
        curr = _snap(frame_deficit=150)  # +50, over threshold
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=2,
            last_recovery_at=None,
            recovery_attempts_for_track=0,
            now=100.0,
        )
        assert action is RecoveryAction.RECOVER

    def test_deficit_growth_at_exact_threshold_counts(self) -> None:
        # Boundary: 25 is the threshold. >= triggers, < doesn't.
        prev = _snap(frame_deficit=100, sampled_at=98.0)
        curr = _snap(frame_deficit=125)  # +25, exactly at threshold
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=2,
            last_recovery_at=None,
            recovery_attempts_for_track=0,
            now=100.0,
        )
        assert action is RecoveryAction.RECOVER


class TestShouldRecoverThrottle:
    """Recovery throttle: after a recovery attempt fires, suppress
    further wedge signals for 60s so we don't churn while voice is
    re-handshaking."""

    def test_within_60s_of_last_recovery_returns_none(self) -> None:
        # Already recovered 30s ago. Even a confirmed wedge sample
        # gets suppressed.
        prev = _snap(voice_connected=False, sampled_at=98.0)
        curr = _snap(voice_connected=False)
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=2,
            last_recovery_at=70.0,  # 30s ago at now=100.0
            recovery_attempts_for_track=0,  # counter not yet bumped
            now=100.0,
        )
        assert action is RecoveryAction.NONE

    def test_past_60s_throttle_fires_again(self) -> None:
        # 61s after the previous recovery; throttle has lifted. Same
        # signal that was suppressed above should now act.
        prev = _snap(voice_connected=False, sampled_at=98.0)
        curr = _snap(voice_connected=False)
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=2,
            last_recovery_at=39.0,  # 61s ago at now=100.0
            recovery_attempts_for_track=0,
            now=100.0,
        )
        assert action is RecoveryAction.RECOVER


class TestShouldRecoverPerTrackBudget:
    """Decision-3 semantics from the design review: ONE retry per
    track, then give up and skip. The counter must NOT reset on
    healthy-audio gaps within the same track."""

    def test_first_wedge_on_track_recovers(self) -> None:
        prev = _snap(voice_connected=False, sampled_at=98.0)
        curr = _snap(voice_connected=False)
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=2,
            last_recovery_at=None,
            recovery_attempts_for_track=0,
            now=100.0,
        )
        assert action is RecoveryAction.RECOVER

    def test_second_wedge_on_same_track_gives_up_and_skips(self) -> None:
        # Already tried once on track-A, throttle has lifted, signal
        # still bad -> skip the track instead of looping retries.
        prev = _snap(voice_connected=False, sampled_at=98.0)
        curr = _snap(voice_connected=False)
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=2,
            last_recovery_at=30.0,  # 70s ago, past throttle
            recovery_attempts_for_track=1,
            now=100.0,
        )
        assert action is RecoveryAction.GIVE_UP_AND_SKIP


class TestShouldRecoverBoundaryAndOrdering:
    """Tests that lock in the order in which conditions are evaluated.
    Important so refactors don't accidentally change the precedence
    (e.g. allowing a paused-but-wedged sample to trigger recovery)."""

    def test_paused_takes_precedence_over_wedge_signal(self) -> None:
        # User paused; even though voice_connected=False (which would
        # normally trigger), we MUST NOT act. Paused players legitimately
        # have no audio flow.
        prev = _snap(is_paused=True, voice_connected=False, sampled_at=98.0)
        curr = _snap(is_paused=True, voice_connected=False)
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=5,  # plenty of wedge confirmation
            last_recovery_at=None,
            recovery_attempts_for_track=0,
            now=100.0,
        )
        assert action is RecoveryAction.NONE

    def test_no_track_takes_precedence_over_attempts_budget(self) -> None:
        # Edge case: track_identifier is None AND attempts_for_track
        # >= 1 (shouldn't normally happen, but make sure no-track
        # short-circuits before the budget check fires GIVE_UP_AND_SKIP).
        prev = _snap(track_identifier=None, is_playing=False, sampled_at=98.0)
        curr = _snap(track_identifier=None, is_playing=False)
        action = should_recover(
            curr=curr,
            prev=prev,
            consecutive_wedge_samples=10,
            last_recovery_at=None,
            recovery_attempts_for_track=1,
            now=100.0,
        )
        assert action is RecoveryAction.NONE
