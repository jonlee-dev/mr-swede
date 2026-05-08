"""Unit tests for the strict liveness logic in src.http.

We don't test FastAPI routing here -- that's just framework plumbing.
The valuable thing to lock down is `_evaluate_liveness`, the pure
function that turns a `_LivenessSnapshot` into (alive, reason). Today's
incident (2026-05-08) happened because the loose health check let a
silently-degraded bot keep declaring itself fine; these tests make
sure every "not alive" branch is reachable and reports the right
reason tag.
"""

from src.http import (
    _GATEWAY_FRESHNESS_SECONDS,
    _evaluate_liveness,
    _LivenessSnapshot,
)


def _healthy_snap(**overrides: object) -> _LivenessSnapshot:
    """Construct a baseline snapshot that evaluates as alive=True.

    Tests override one field at a time to verify the corresponding
    failure branch fires. Keeps each test self-explanatory.
    """
    base = {
        "bot_initialized": True,
        "is_ready": True,
        "is_closed": False,
        "ws_open": True,
        "last_socket_event_age_seconds": 5.0,
        "startup_error": None,
    }
    base.update(overrides)
    return _LivenessSnapshot(**base)  # type: ignore[arg-type]


class TestEvaluateLiveness:
    """One test per failure branch + a positive baseline."""

    def test_baseline_is_alive(self) -> None:
        alive, reason = _evaluate_liveness(_healthy_snap())
        assert alive is True
        assert reason == ""

    def test_startup_error_fails_first(self) -> None:
        # Even if everything else is fine, a startup_error short-
        # circuits the rest. Order matters because the most-specific
        # cause should be reported.
        alive, reason = _evaluate_liveness(_healthy_snap(startup_error="bad token"))
        assert alive is False
        assert "startup_error" in reason
        assert "bad token" in reason

    def test_bot_not_initialized(self) -> None:
        alive, reason = _evaluate_liveness(_healthy_snap(bot_initialized=False))
        assert alive is False
        assert reason == "bot_not_initialized"

    def test_not_ready(self) -> None:
        alive, reason = _evaluate_liveness(_healthy_snap(is_ready=False))
        assert alive is False
        assert reason == "not_ready"

    def test_closed(self) -> None:
        # bot.close() was called -- intentional shutdown in progress.
        alive, reason = _evaluate_liveness(_healthy_snap(is_closed=True))
        assert alive is False
        assert reason == "bot_closed"

    def test_websocket_not_open(self) -> None:
        # The 2026-05-08 incident shape: bot.is_ready() stayed True
        # but the underlying WS was actually closed. ws_open is the
        # check that catches this.
        alive, reason = _evaluate_liveness(_healthy_snap(ws_open=False))
        assert alive is False
        assert reason == "websocket_not_open"

    def test_no_gateway_events_received(self) -> None:
        # The bot has been up but has never received a gateway message.
        # Could mean the WS handshake completed but didn't flow into
        # event dispatch -- treat as unhealthy.
        alive, reason = _evaluate_liveness(_healthy_snap(last_socket_event_age_seconds=None))
        assert alive is False
        assert reason == "no_gateway_events_received"

    def test_gateway_stale(self) -> None:
        # Last event arrived, but too long ago. Even if ws_open says
        # True, no recent traffic means the connection is silently dead.
        # 90s is the threshold -- test at 91s to be just over.
        stale_age = _GATEWAY_FRESHNESS_SECONDS + 1.0
        alive, reason = _evaluate_liveness(_healthy_snap(last_socket_event_age_seconds=stale_age))
        assert alive is False
        assert reason.startswith("gateway_stale_")
        # The reason includes the age so the operator can see how
        # stale it actually is, not just "older than threshold".
        assert "91.0" in reason

    def test_gateway_fresh_at_exact_threshold_is_still_alive(self) -> None:
        # Boundary check: an event exactly _GATEWAY_FRESHNESS_SECONDS
        # ago is fresh enough. The check is `> threshold`, not `>=`.
        alive, reason = _evaluate_liveness(
            _healthy_snap(last_socket_event_age_seconds=_GATEWAY_FRESHNESS_SECONDS)
        )
        assert alive is True
        assert reason == ""

    def test_failure_priority_order(self) -> None:
        # Multiple things can be wrong at once; we report the most
        # specific. Confirm a stale gateway with ws_open=False
        # surfaces the websocket problem (more specific) rather than
        # the staleness (which would be an artifact of the WS being
        # closed anyway).
        snap = _healthy_snap(
            ws_open=False,
            last_socket_event_age_seconds=999.0,
        )
        alive, reason = _evaluate_liveness(snap)
        assert alive is False
        assert reason == "websocket_not_open"
