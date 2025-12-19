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

from src.bot import create_bot, get_bot_token
from src.config.logging import get_logger, setup_logging
from src.config.secrets import get_secrets
from src.config.settings import get_settings

logger = get_logger(__name__)


# Global bot instance for health check access
_bot = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan manager for startup/shutdown."""
    global _bot
    
    settings = get_settings()
    
    logger.info(
        "Starting Mr. Swede bot...",
        bot_name=settings.discord_bot_name,
        environment=settings.environment,
    )
    
    # Get the bot token
    try:
        token = get_bot_token()
    except ValueError as e:
        logger.error("Failed to get bot token", error=str(e))
        raise
    
    # Create and start the bot
    _bot = create_bot()
    
    # Start bot in background task
    bot_task = asyncio.create_task(_bot.start(token))
    
    yield
    
    # Cleanup
    logger.info("Shutting down...")
    if _bot:
        await _bot.close()
    bot_task.cancel()
    try:
        await bot_task
    except asyncio.CancelledError:
        pass


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
    """Health check endpoint for Cloud Run."""
    global _bot
    
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
    
    # Validate secrets are available
    secrets = get_secrets(discord_bot_name=settings.discord_bot_name)
    if not secrets.discord:
        logger.error(
            "Discord secrets not found",
            bot_name=settings.discord_bot_name,
            hint="Check GSM configuration or set DISCORD_TOKEN env var",
        )
        sys.exit(1)
    
    logger.info(
        "Starting server",
        host=settings.host,
        port=settings.port,
        environment=settings.environment,
        bot_name=settings.discord_bot_name,
    )
    
    # Run with uvicorn
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
