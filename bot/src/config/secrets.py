"""Google Secret Manager integration.

Two distinct secrets:

  discord-bot-secrets   -- JSON blob, multi-bot. Format:
      {
        "mr-swede": {"id": "...", "token": "...", "public_key": "..."},
        ...
      }
    Held over from when this repo hosted multiple Discord bots. We
    still support nested objects AND dot-notation keys
    (`"mr-swede.token": "..."`) for compatibility with how the
    existing GSM secret is structured.

  valheim-server-password -- plain UTF-8 string. The server password
    seeded out-of-band and consumed by both the Valheim container and
    /valheim status (so users see the password in the channel without
    having to ask).
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
    valheim_password: str | None


class SecretManager:
    """Pulls secrets from GSM, with env-var fallback for local dev."""

    # The exact resource paths are project-specific; in production we look
    # them up via *_SECRET_PATH env vars so we don't hardcode project
    # numbers here.
    DEFAULT_SECRET_PATHS: dict[str, str] = {
        "discord": "",  # Set via DISCORD_SECRET_PATH env var
        "valheim_password": "",  # Set via VALHEIM_PASSWORD_SECRET_PATH env var
    }

    def __init__(
        self,
        project_id: str | None = None,
        discord_bot_name: str = "mr-swede",
    ):
        self._project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self._discord_bot_name = discord_bot_name
        self._client: Any = None
        # JSON-parsed cache (used for the discord secret).
        self._json_cache: dict[str, Any] = {}
        # Plain-string cache (used for the valheim password).
        self._string_cache: dict[str, str] = {}

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
        """Resolve a secret path: env var override > project-id default > empty."""
        env_var = f"{secret_type.upper()}_SECRET_PATH"
        if os.environ.get(env_var):
            return os.environ[env_var]
        if self._project_id:
            if secret_type == "discord":
                return f"projects/{self._project_id}/secrets/discord-bot-secrets/versions/latest"
            if secret_type == "valheim_password":
                return (
                    f"projects/{self._project_id}/secrets/valheim-server-password/versions/latest"
                )
        return self.DEFAULT_SECRET_PATHS.get(secret_type, "")

    def _fetch_secret_json(self, secret_path: str) -> dict[str, Any] | None:
        """Fetch a JSON-parsed secret. Used for the multi-bot Discord secret."""
        if not secret_path:
            logger.warning("Empty secret path provided")
            return None
        if secret_path in self._json_cache:
            cached: dict[str, Any] | None = self._json_cache[secret_path]
            return cached
        if not self.client:
            logger.error("Secret Manager client not available - cannot fetch secrets")
            return None

        logger.info("Fetching secret from GSM", path=secret_path)
        try:
            response = self.client.access_secret_version(request={"name": secret_path})
            parsed: dict[str, Any] = json.loads(response.payload.data.decode("UTF-8"))
            self._json_cache[secret_path] = parsed
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

    def _fetch_secret_string(self, secret_path: str) -> str | None:
        """Fetch a plain UTF-8 string secret. Used for the valheim password."""
        if not secret_path:
            return None
        if secret_path in self._string_cache:
            return self._string_cache[secret_path]
        if not self.client:
            logger.error("Secret Manager client not available - cannot fetch secrets")
            return None

        logger.info("Fetching string secret from GSM", path=secret_path)
        try:
            response = self.client.access_secret_version(request={"name": secret_path})
            # Annotate explicitly: self.client is typed Any (lazy-loaded
            # to tolerate missing dep), so the decoded chain inherits Any
            # without this hint and mypy's no-any-return fires.
            decoded: str = response.payload.data.decode("UTF-8").strip()
            self._string_cache[secret_path] = decoded
            return decoded
        except Exception as e:
            logger.error(
                "Failed to fetch string secret",
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

    def get_valheim_password(self) -> str | None:
        """Return the Valheim server password (plain string).

        Checked in order: VALHEIM_PASSWORD env var → GSM secret. The
        env var is for local dev; in Cloud Run we always go through
        GSM via VALHEIM_PASSWORD_SECRET_PATH.
        """
        if os.environ.get("VALHEIM_PASSWORD"):
            logger.info("Using VALHEIM_PASSWORD from environment")
            return os.environ["VALHEIM_PASSWORD"]
        secret_path = self._get_secret_path("valheim_password")
        return self._fetch_secret_string(secret_path)

    def get_all_secrets(self) -> AppSecrets:
        return AppSecrets(
            discord=self.get_discord_secrets(),
            valheim_password=self.get_valheim_password(),
        )

    def clear_cache(self) -> None:
        self._json_cache.clear()
        self._string_cache.clear()


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
