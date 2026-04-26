"""Main entry point for Mr. Swede bot.

This module handles both Discord bot operation and a health check HTTP server
for Cloud Run deployments.
"""

import asyncio
import signal
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI

from src.config.logging import get_logger, setup_logging
from src.config.settings import get_settings

logger = get_logger(__name__)


def _handle_exception(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Global exception handler for asyncio tasks.
    
    This prevents unhandled exceptions in background tasks from crashing the bot.
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
    
    # Don't crash - just log the error


# Global state for health checks
_bot = None
_bot_task = None
_startup_error: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan manager for startup/shutdown.
    
    Important: We start the HTTP server FIRST, then connect to Discord.
    This ensures Cloud Run health checks pass while the bot is connecting.
    """
    global _bot, _bot_task, _startup_error
    
    settings = get_settings()
    
    # Set global exception handler to prevent crashes from background tasks
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_handle_exception)
    
    logger.info(
        "Server starting...",
        bot_name=settings.discord_bot_name,
        environment=settings.environment,
    )
    
    # Start bot connection in background (don't block server startup)
    _bot_task = asyncio.create_task(_start_bot_async(settings))
    
    yield  # Server is now accepting requests
    
    # Cleanup on shutdown
    logger.info("Shutting down...")
    if _bot:
        await _bot.close()
    if _bot_task:
        _bot_task.cancel()
        try:
            await _bot_task
        except asyncio.CancelledError:
            pass


async def _start_bot_async(settings) -> None:
    """Start the Discord bot asynchronously.
    
    This runs after the HTTP server is ready, so health checks work.
    """
    global _bot, _startup_error
    
    # Import here to avoid circular imports and defer loading
    from src.bot import create_bot, get_bot_token
    
    try:
        token = get_bot_token()
    except ValueError as e:
        _startup_error = str(e)
        logger.error(
            "Failed to get bot token",
            error=str(e),
            hint="Check GSM configuration or set DISCORD_TOKEN env var",
        )
        return  # Don't crash - keep health endpoint running for debugging
    
    logger.info("Connecting to Discord...")
    _bot = create_bot()
    
    # Keep trying to stay connected
    while True:
        try:
            await _bot.start(token)
        except asyncio.CancelledError:
            logger.info("Bot task cancelled")
            break
        except Exception as e:
            _startup_error = str(e)
            logger.error("Bot connection failed", error=str(e))
            # Wait before reconnecting
            logger.info("Waiting 30 seconds before reconnecting...")
            await asyncio.sleep(30)
            # Clear error state for reconnection attempt
            _startup_error = None
            logger.info("Attempting to reconnect...")


# FastAPI app for Cloud Run health checks
app = FastAPI(
    title="Mr. Swede Bot",
    description="Discord bot health check endpoint",
    lifespan=lifespan,
)


@app.get("/")
async def root() -> dict:
    """Root endpoint."""
    settings = get_settings()
    return {
        "status": "ok",
        "service": "mr-swede",
        "bot_name": settings.discord_bot_name,
    }


@app.get("/health")
async def health() -> dict:
    """Health check endpoint for Cloud Run.
    
    Returns 200 as long as the server is running.
    Cloud Run uses this to determine if the container is healthy.
    """
    global _bot, _startup_error
    
    if _startup_error:
        return {
            "status": "error",
            "bot_ready": False,
            "error": _startup_error,
        }
    
    if _bot and _bot.is_ready():
        return {
            "status": "healthy",
            "bot_ready": True,
            "guilds": len(_bot.guilds),
            "latency_ms": round(_bot.latency * 1000, 2),
        }
    
    return {
        "status": "starting",
        "bot_ready": False,
    }


@app.get("/metrics")
async def metrics() -> dict:
    """Basic metrics endpoint."""
    global _bot
    
    if not _bot:
        return {"error": "Bot not initialized"}
    
    return {
        "guilds": len(_bot.guilds) if _bot.guilds else 0,
        "latency_ms": round(_bot.latency * 1000, 2) if _bot.latency else None,
        "is_ready": _bot.is_ready(),
    }


def main() -> None:
    """Main entry point."""
    setup_logging()
    settings = get_settings()
    
    logger.info(
        "Starting server",
        host=settings.host,
        port=settings.port,
        environment=settings.environment,
        bot_name=settings.discord_bot_name,
    )
    
    # Run with uvicorn - bot starts asynchronously via lifespan
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="warning",  # Reduce uvicorn noise, we use structlog
    )


def run_bot_only() -> None:
    """Run only the Discord bot without the HTTP server.
    
    Useful for local development or non-Cloud Run deployments.
    """
    setup_logging()
    settings = get_settings()
    
    # Get the bot token
    try:
        token = get_bot_token()
    except ValueError as e:
        logger.error("Failed to get bot token", error=str(e))
        sys.exit(1)
    
    bot = create_bot()
    
    # Handle graceful shutdown
    def handle_shutdown(signum, frame):
        logger.info("Received shutdown signal")
        asyncio.get_event_loop().create_task(bot.close())
        sys.exit(0)
    
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    logger.info(
        "Starting bot (standalone mode)",
        bot_name=settings.discord_bot_name,
    )
    bot.run(token)


if __name__ == "__main__":
    # Check if running in standalone mode
    if "--standalone" in sys.argv:
        run_bot_only()
    else:
        main()
