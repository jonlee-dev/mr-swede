"""FastAPI server for Cloud Run health checks.

Cloud Run probes `/health` to decide whether the container is alive. We
start the HTTP server FIRST and connect to Discord asynchronously in
the background -- so health checks pass while the bot is still connecting.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

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
    """Cloud Run readiness probe. Always 200 -- payload describes bot state."""
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


@app.get("/metrics")
async def metrics() -> dict:
    if not _bot:
        return {"error": "Bot not initialized"}
    return {
        "guilds": len(_bot.guilds) if _bot.guilds else 0,
        "latency_ms": round(_bot.latency * 1000, 2) if _bot.latency else None,
        "is_ready": _bot.is_ready(),
    }
