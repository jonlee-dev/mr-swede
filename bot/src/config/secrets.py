"""Google Secret Manager integration for the Discord bot token.

The discord-bot-secrets GSM secret is a JSON object of the form:

    {
      "mr-swede": {"id": "...", "token": "...", "public_key": "..."},
      ...
    }

A single secret holding multiple bots is a holdover from when this repo
hosted multiple Discord bots. We still support nested objects AND
dot-notation keys (`"mr-swede.token": "..."`) for compatibility with how
the existing GSM secret is structured -- changing that secret in place
would require coordinating with anyone else reading it.
"""

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from src.config.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class DiscordBotSecrets:
    """Discord bot credentials for one bot."""

    id: str
    token: str
    public_key: str


@dataclass(frozen=True)
class AppSecrets:
    """All application secrets."""

    discord: DiscordBotSecrets | None


class SecretManager:
    """Pulls the discord-bot-secrets JSON from GSM, with env-var fallback."""

    # The exact resource path is project-specific; in production we look it up
    # via DISCORD_SECRET_PATH so we don't hardcode the project number here.
    DEFAULT_SECRET_PATHS: dict[str, str] = {
        "discord": "",  # Set via DISCORD_SECRET_PATH env var
    }

    def __init__(
        self,
        project_id: str | None = None,
        discord_bot_name: str = "mr-swede",
    ):
        self._project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self._discord_bot_name = discord_bot_name
        self._client: Any = None
        self._cache: dict[str, Any] = {}

    @property
    def client(self) -> Any:
        """Lazy-load the GSM client; tolerate missing dep / no creds."""
        if self._client is None:
            try:
                from google.cloud import secretmanager

                self._client = secretmanager.SecretManagerServiceClient()
                logger.info("Secret Manager client created successfully")
            except ImportError as e:
                logger.error("google-cloud-secret-manager not installed", error=str(e))
                return None
            except Exception as e:
                logger.error(
                    "Failed to create Secret Manager client",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                return None
        return self._client

    def _get_secret_path(self, secret_type: str) -> str:
        env_var = f"{secret_type.upper()}_SECRET_PATH"
        if os.environ.get(env_var):
            return os.environ[env_var]
        # Build a default if project_id is known.
        if self._project_id and secret_type == "discord":
            return f"projects/{self._project_id}/secrets/discord-bot-secrets/versions/latest"
        return self.DEFAULT_SECRET_PATHS.get(secret_type, "")

    def _fetch_secret_json(self, secret_path: str) -> dict[str, Any] | None:
        if not secret_path:
            logger.warning("Empty secret path provided")
            return None
        if secret_path in self._cache:
            cached: dict[str, Any] | None = self._cache[secret_path]
            return cached
        if not self.client:
            logger.error("Secret Manager client not available - cannot fetch secrets")
            return None

        logger.info("Fetching secret from GSM", path=secret_path)
        try:
            response = self.client.access_secret_version(request={"name": secret_path})
            parsed: dict[str, Any] = json.loads(response.payload.data.decode("UTF-8"))
            self._cache[secret_path] = parsed
            logger.info("Loaded secret", path=secret_path, keys=list(parsed.keys()))
            return parsed
        except json.JSONDecodeError as e:
            logger.error("Secret is not valid JSON", path=secret_path, error=str(e))
            return None
        except Exception as e:
            logger.error(
                "Failed to fetch secret",
                path=secret_path,
                error=str(e),
                error_type=type(e).__name__,
            )
            return None

    def get_discord_secrets(self, bot_name: str | None = None) -> DiscordBotSecrets | None:
        bot_name = bot_name or self._discord_bot_name
        logger.info("Getting Discord secrets", bot_name=bot_name)

        # Local-dev fallback: env var beats GSM lookup.
        if os.environ.get("DISCORD_TOKEN"):
            logger.info("Using DISCORD_TOKEN from environment")
            return DiscordBotSecrets(
                id=os.environ.get("DISCORD_APPLICATION_ID", ""),
                token=os.environ["DISCORD_TOKEN"],
                public_key=os.environ.get("DISCORD_PUBLIC_KEY", ""),
            )

        secret_path = self._get_secret_path("discord")
        data = self._fetch_secret_json(secret_path)
        if not data:
            return None

        # The GSM secret historically stored bots two ways. Try nested first.
        try:
            if bot_name in data and isinstance(data[bot_name], dict):
                bot_data = data[bot_name]
                return DiscordBotSecrets(
                    id=bot_data.get("id", ""),
                    token=bot_data["token"],
                    public_key=bot_data.get("public_key", ""),
                )

            # Fall back to dot-notation flat keys.
            token_key = f"{bot_name}.token"
            if token_key in data:
                return DiscordBotSecrets(
                    id=data.get(f"{bot_name}.id", ""),
                    token=data[token_key],
                    public_key=data.get(f"{bot_name}.public_key", ""),
                )

            logger.error("Bot not found in discord secrets", bot_name=bot_name)
            return None
        except KeyError as e:
            logger.error("Missing key in discord secrets", key=str(e), bot_name=bot_name)
            return None

    def get_all_secrets(self) -> AppSecrets:
        return AppSecrets(discord=self.get_discord_secrets())

    def clear_cache(self) -> None:
        self._cache.clear()


_secret_manager: SecretManager | None = None


def get_secret_manager(
    discord_bot_name: str = "mr-swede",
    force_new: bool = False,
) -> SecretManager:
    global _secret_manager
    if _secret_manager is None or force_new:
        _secret_manager = SecretManager(discord_bot_name=discord_bot_name)
    return _secret_manager


@lru_cache
def get_secrets(discord_bot_name: str = "mr-swede") -> AppSecrets:
    """Get all application secrets. Cached for the process lifetime."""
    return get_secret_manager(discord_bot_name=discord_bot_name).get_all_secrets()
