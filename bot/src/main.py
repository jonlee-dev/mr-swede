"""Entrypoint: start uvicorn serving the FastAPI app from src.http.

The Discord bot is started in the background by the FastAPI lifespan, so
health checks pass while the bot is connecting. See src/http.py for details.
"""

import uvicorn

from src.config.logging import get_logger, setup_logging
from src.config.settings import get_settings

logger = get_logger(__name__)


def main() -> None:
    setup_logging()
    settings = get_settings()
    logger.info(
        "Starting server",
        host=settings.host,
        port=settings.port,
        environment=settings.environment,
        bot_name=settings.discord_bot_name,
    )
    uvicorn.run(
        "src.http:app",
        host=settings.host,
        port=settings.port,
        log_level="warning",  # Reduce uvicorn noise; we use structlog
    )


if __name__ == "__main__":
    main()
