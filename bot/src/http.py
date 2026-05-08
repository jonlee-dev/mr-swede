"""FastAPI server for Cloud Run health checks.

We expose two endpoints with intentionally different semantics:

  - GET /health -- always 200; payload describes current bot state.
                   Useful for human eyeballs / curl. Stays soft so a
                   degraded-but-still-running bot is debuggable.
  - GET /livez  -- strict liveness probe. 200 if the gateway WebSocket
                   is open AND we've received an event recently;
                   503 otherwise. Cloud Run's `liveness_probe` hits
                   this endpoint -- repeated 503s trigger a container
                   restart, replacing wedged instances automatically.

Why a separate strict probe? The 2026-05-08 incident: bot's gateway
WS silently died, but `bot.is_ready()` (never resets after first
READY) and `bot.latency` (caches the last heartbeat ack) both kept
reporting "healthy" for ~5 hours. Cloud Run's default TCP probe was
satisfied that uvicorn was listening, so the container was never
replaced. The bot just sat unable to receive interactions while
chewing through the Discord IDENTIFY rate limit on each retry.

We start the HTTP server FIRST and connect to Discord asynchronously
in the background -- so health checks pass while the bot is still
connecting.
"""

import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.bot import MrSwede, create_bot, get_bot_token
from src.config.logging import get_logger
from src.config.settings import Settings, get_settings

logger = get_logger(__name__)


# Module-level state for health endpoints. Acceptable because this module
# is the single FastAPI app -- not reused across processes.
_bot: MrSwede | None = None
_bot_task: asyncio.Task[None] | None = None
_startup_error: str | None = None


def _handle_exception(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Top-level asyncio exception handler.

    Background task crashes get logged, not re-raised -- otherwise a single
    failed cog would take down the whole process.
    """
    exception = context.get("exception")
    message = context.get("message", "Unknown error")
    if exception:
        logger.error(
            "Unhandled exception in async task",
            error=str(exception),
            error_type=type(exception).__name__,
            message=message,
        )
    else:
        logger.error("Async task error", message=message)


async def _run_bot(settings: Settings) -> None:
    """Connect to Discord and reconnect on failure.

    Started by the FastAPI lifespan AFTER uvicorn binds the port, so /health
    works during the connect/reconnect window.
    """
    global _bot, _startup_error

    try:
        token = get_bot_token()
    except ValueError as e:
        _startup_error = str(e)
        logger.error(
            "Failed to get bot token",
            error=str(e),
            hint="Check GSM configuration or set DISCORD_TOKEN env var",
        )
        return

    logger.info("Connecting to Discord...")
    _bot = create_bot()

    while True:
        try:
            await _bot.start(token)
        except asyncio.CancelledError:
            logger.info("Bot task cancelled")
            break
        except Exception as e:
            _startup_error = str(e)
            logger.error("Bot connection failed", error=str(e))
            logger.info("Waiting 30 seconds before reconnecting...")
            await asyncio.sleep(30)
            _startup_error = None
            logger.info("Attempting to reconnect...")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start bot in background, then yield (HTTP serving). Stop bot on shutdown."""
    global _bot_task

    settings = get_settings()
    asyncio.get_running_loop().set_exception_handler(_handle_exception)
    logger.info(
        "Server starting",
        bot_name=settings.discord_bot_name,
        environment=settings.environment,
    )

    _bot_task = asyncio.create_task(_run_bot(settings))

    yield

    logger.info("Shutting down")
    if _bot:
        await _bot.close()
    if _bot_task:
        _bot_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _bot_task


app = FastAPI(
    title="Mr. Swede Bot",
    description="Discord bot health check endpoint",
    lifespan=lifespan,
)


@app.get("/")
async def root() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "service": "mr-swede",
        "bot_name": settings.discord_bot_name,
    }


@app.get("/health")
async def health() -> dict:
    """Soft health endpoint. Always 200 -- payload describes bot state.

    Used for human eyeballs / curl. NOT used by Cloud Run's liveness
    probe (that hits `/livez` which has strict semantics). A degraded
    bot still returns 200 here so it's debuggable via the same URL.
    """
    if _startup_error:
        return {"status": "error", "bot_ready": False, "error": _startup_error}
    if _bot and _bot.is_ready():
        return {
            "status": "healthy",
            "bot_ready": True,
            "guilds": len(_bot.guilds),
            "latency_ms": round(_bot.latency * 1000, 2),
        }
    return {"status": "starting", "bot_ready": False}


# ---------------------------------------------------------------------------
# /livez -- strict liveness probe for Cloud Run
# ---------------------------------------------------------------------------

# Maximum age of the last gateway event before we declare the WS dead.
# Discord sends heartbeats every ~41s; 90s = ~2 missed heartbeats
# before we fail the probe. With Cloud Run's default failureThreshold
# of 5 consecutive failures and periodSeconds=60, a wedge needs to
# persist ~5 minutes before the container is killed -- generous for
# transient blips.
_GATEWAY_FRESHNESS_SECONDS = 90.0


@dataclass(frozen=True)
class _LivenessSnapshot:
    """Inputs to `_evaluate_liveness`. Decouples the decision logic from
    the `_bot` global so unit tests can drive every branch without
    constructing a real Discord client.
    """

    # Bot state -- mirrors what the live `_bot` would expose.
    bot_initialized: bool
    is_ready: bool
    is_closed: bool
    ws_open: bool

    # Gateway freshness.
    last_socket_event_age_seconds: float | None  # None == bot has never received an event

    # Startup error string (None when no error).
    startup_error: str | None


def _evaluate_liveness(snap: _LivenessSnapshot) -> tuple[bool, str]:
    """Pure function deciding whether the bot is currently alive.

    Returns (alive, reason). `reason` is empty when alive=True,
    otherwise a short tag suitable for /livez's response body. Every
    branch is unit-tested in tests/unit/test_http.py.

    Order matters: report the most-specific failure first so
    debugging is easier.
    """
    if snap.startup_error is not None:
        return False, f"startup_error: {snap.startup_error}"
    if not snap.bot_initialized:
        return False, "bot_not_initialized"
    if not snap.is_ready:
        return False, "not_ready"
    if snap.is_closed:
        return False, "bot_closed"
    if not snap.ws_open:
        return False, "websocket_not_open"
    if snap.last_socket_event_age_seconds is None:
        return False, "no_gateway_events_received"
    if snap.last_socket_event_age_seconds > _GATEWAY_FRESHNESS_SECONDS:
        return False, f"gateway_stale_{snap.last_socket_event_age_seconds:.1f}s"
    return True, ""


def _snapshot_liveness(now: float) -> _LivenessSnapshot:
    """Build a snapshot from the live `_bot` global. Wrapper so the
    pure decision logic stays test-friendly.
    """
    bot = _bot
    if bot is None:
        return _LivenessSnapshot(
            bot_initialized=False,
            is_ready=False,
            is_closed=False,
            ws_open=False,
            last_socket_event_age_seconds=None,
            startup_error=_startup_error,
        )
    ws = getattr(bot, "ws", None)
    last_event = getattr(bot, "last_socket_event_time", None)
    return _LivenessSnapshot(
        bot_initialized=True,
        is_ready=bot.is_ready(),
        is_closed=bot.is_closed(),
        ws_open=bool(ws is not None and getattr(ws, "open", False)),
        last_socket_event_age_seconds=(now - last_event) if last_event is not None else None,
        startup_error=_startup_error,
    )


@app.get("/livez")
async def livez() -> JSONResponse:
    """Strict liveness probe for Cloud Run. 200 alive, 503 not.

    Cloud Run's `liveness_probe` (configured in
    `infra/modules/gcp-bot-runtime/service.tf`) hits this endpoint
    every 60s. After 5 consecutive 503s (~5 min unhealthy), Cloud Run
    kills the container and `min-instances=1` triggers a fresh start.

    The 5-min grace is intentional: it's long enough to ride out a
    transient Cloudflare blip (Discord rate limit, brief network
    hiccup) without flapping into a restart-storm, and short enough
    that a real wedge gets fixed without operator intervention.
    """
    snap = _snapshot_liveness(time.monotonic())
    alive, reason = _evaluate_liveness(snap)
    body: dict[str, Any] = {"alive": alive}
    if not alive:
        body["reason"] = reason
        return JSONResponse(body, status_code=503)
    return JSONResponse(body, status_code=200)


@app.get("/metrics")
async def metrics() -> dict:
    if not _bot:
        return {"error": "Bot not initialized"}
    return {
        "guilds": len(_bot.guilds) if _bot.guilds else 0,
        "latency_ms": round(_bot.latency * 1000, 2) if _bot.latency else None,
        "is_ready": _bot.is_ready(),
    }
