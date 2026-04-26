"""Google Secret Manager integration for JSON-formatted secrets.

This module provides a clean abstraction for loading secrets from GSM
where secrets are stored as JSON objects rather than plain strings.

Secret Structure in GSM:
- blizzard-secrets: {"client_id": "...", "client_secret": "..."}
- discord-bot-secrets: {"mr-swede": {"id": "...", "token": "...", "public_key": "..."}, ...}
- spotify-secrets: {"client_id": "...", "client_secret": "..."}
"""

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from src.config.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class BlizzardSecrets:
    """Blizzard API credentials."""
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class DiscordBotSecrets:
    """Discord bot credentials."""
    id: str
    token: str
    public_key: str


@dataclass(frozen=True)
class SpotifySecrets:
    """Spotify API credentials."""
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class AppSecrets:
    """All application secrets."""
    blizzard: BlizzardSecrets | None
    discord: DiscordBotSecrets | None
    spotify: SpotifySecrets | None


class SecretManager:
    """Manages secrets from Google Secret Manager with JSON parsing."""
    
    # Default secret resource paths (can be overridden via env vars)
    DEFAULT_SECRET_PATHS = {
        "blizzard": "projects/749144818572/secrets/blizzard-secrets/versions/latest",
        "discord": "projects/749144818572/secrets/discord-bot-secrets/versions/latest",
        "spotify": "projects/749144818572/secrets/spotify-secrets/versions/latest",
    }
    
    def __init__(
        self,
        project_id: str | None = None,
        discord_bot_name: str = "mr-swede",
    ):
        """Initialize the secret manager.
        
        Args:
            project_id: GCP project ID (optional, used for simpler secret names)
            discord_bot_name: Which Discord bot credentials to use 
                             ("mr-swede" or "ow2-ranked-bot")
        """
        self._project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self._discord_bot_name = discord_bot_name
        self._client = None
        self._cache: dict[str, Any] = {}
    
    @property
    def client(self):
        """Lazy-load the Secret Manager client."""
        if self._client is None:
            try:
                from google.cloud import secretmanager
                self._client = secretmanager.SecretManagerServiceClient()
                logger.info("Secret Manager client created successfully")
            except ImportError as e:
                logger.error("google-cloud-secret-manager not installed", error=str(e))
                return None
            except Exception as e:
                logger.error("Failed to create Secret Manager client", error=str(e), error_type=type(e).__name__)
                return None
        return self._client
    
    def _get_secret_path(self, secret_type: str) -> str:
        """Get the full secret resource path.
        
        Args:
            secret_type: One of "blizzard", "discord", "spotify"
            
        Returns:
            Full resource path for the secret
        """
        # Check for environment variable override
        env_var = f"{secret_type.upper()}_SECRET_PATH"
        if os.environ.get(env_var):
            return os.environ[env_var]
        
        return self.DEFAULT_SECRET_PATHS.get(secret_type, "")
    
    def _fetch_secret_json(self, secret_path: str) -> dict[str, Any] | None:
        """Fetch and parse a JSON secret from GSM.
        
        Args:
            secret_path: Full resource path to the secret
            
        Returns:
            Parsed JSON dict or None if failed
        """
        if not secret_path:
            logger.warning("Empty secret path provided")
            return None
        
        # Check cache first
        if secret_path in self._cache:
            logger.debug("Using cached secret", path=secret_path)
            return self._cache[secret_path]
        
        if not self.client:
            logger.error("Secret Manager client not available - cannot fetch secrets")
            return None
        
        logger.info("Fetching secret from GSM", path=secret_path)
        
        try:
            response = self.client.access_secret_version(request={"name": secret_path})
            secret_data = response.payload.data.decode("UTF-8")
            parsed = json.loads(secret_data)
            
            # Cache the result
            self._cache[secret_path] = parsed
            logger.info("Successfully loaded secret", path=secret_path, keys=list(parsed.keys()))
            
            return parsed
        except json.JSONDecodeError as e:
            logger.error("Secret is not valid JSON", path=secret_path, error=str(e))
            return None
        except Exception as e:
            logger.error("Failed to fetch secret", path=secret_path, error=str(e), error_type=type(e).__name__)
            return None
    
    def get_blizzard_secrets(self) -> BlizzardSecrets | None:
        """Get Blizzard API credentials.
        
        Returns:
            BlizzardSecrets or None if not available
        """
        # Try environment variables first (for local dev)
        if os.environ.get("BLIZZARD_CLIENT_ID") and os.environ.get("BLIZZARD_CLIENT_SECRET"):
            return BlizzardSecrets(
                client_id=os.environ["BLIZZARD_CLIENT_ID"],
                client_secret=os.environ["BLIZZARD_CLIENT_SECRET"],
            )
        
        # Fetch from GSM
        secret_path = self._get_secret_path("blizzard")
        data = self._fetch_secret_json(secret_path)
        
        if not data:
            return None
        
        try:
            return BlizzardSecrets(
                client_id=data["client_id"],
                client_secret=data["client_secret"],
            )
        except KeyError as e:
            logger.error("Missing key in blizzard secrets", key=str(e))
            return None
    
    def get_discord_secrets(self, bot_name: str | None = None) -> DiscordBotSecrets | None:
        """Get Discord bot credentials.
        
        Args:
            bot_name: Bot name to use (defaults to instance setting)
                     Options: "mr-swede", "ow2-ranked-bot"
        
        Returns:
            DiscordBotSecrets or None if not available
        """
        bot_name = bot_name or self._discord_bot_name
        logger.info("Getting Discord secrets", bot_name=bot_name)
        
        # Try environment variables first (for local dev)
        if os.environ.get("DISCORD_TOKEN"):
            logger.info("Using DISCORD_TOKEN from environment variable")
            return DiscordBotSecrets(
                id=os.environ.get("DISCORD_APPLICATION_ID", ""),
                token=os.environ["DISCORD_TOKEN"],
                public_key=os.environ.get("DISCORD_PUBLIC_KEY", ""),
            )
        
        # Fetch from GSM
        secret_path = self._get_secret_path("discord")
        data = self._fetch_secret_json(secret_path)
        
        if not data:
            return None
        
        # Handle the nested structure with dot notation keys
        # The secrets have keys like "mr-swede.id", "mr-swede.token", etc.
        try:
            # Try nested object structure first
            if bot_name in data and isinstance(data[bot_name], dict):
                bot_data = data[bot_name]
                return DiscordBotSecrets(
                    id=bot_data.get("id", ""),
                    token=bot_data["token"],
                    public_key=bot_data.get("public_key", ""),
                )
            
            # Try dot notation keys (e.g., "mr-swede.token")
            id_key = f"{bot_name}.id"
            token_key = f"{bot_name}.token"
            public_key_key = f"{bot_name}.public_key"
            
            if token_key in data:
                return DiscordBotSecrets(
                    id=data.get(id_key, ""),
                    token=data[token_key],
                    public_key=data.get(public_key_key, ""),
                )
            
            logger.error("Bot not found in discord secrets", bot_name=bot_name)
            return None
            
        except KeyError as e:
            logger.error("Missing key in discord secrets", key=str(e), bot_name=bot_name)
            return None
    
    def get_spotify_secrets(self) -> SpotifySecrets | None:
        """Get Spotify API credentials.
        
        Returns:
            SpotifySecrets or None if not available
        """
        # Try environment variables first (for local dev)
        if os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET"):
            return SpotifySecrets(
                client_id=os.environ["SPOTIFY_CLIENT_ID"],
                client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
            )
        
        # Fetch from GSM
        secret_path = self._get_secret_path("spotify")
        data = self._fetch_secret_json(secret_path)
        
        if not data:
            return None
        
        try:
            return SpotifySecrets(
                client_id=data["client_id"],
                client_secret=data["client_secret"],
            )
        except KeyError as e:
            logger.error("Missing key in spotify secrets", key=str(e))
            return None
    
    def get_all_secrets(self) -> AppSecrets:
        """Load all application secrets.
        
        Returns:
            AppSecrets with all loaded credentials (some may be None)
        """
        return AppSecrets(
            blizzard=self.get_blizzard_secrets(),
            discord=self.get_discord_secrets(),
            spotify=self.get_spotify_secrets(),
        )
    
    def clear_cache(self) -> None:
        """Clear the secrets cache (useful for testing)."""
        self._cache.clear()


# Global singleton instance
_secret_manager: SecretManager | None = None


def get_secret_manager(
    discord_bot_name: str = "mr-swede",
    force_new: bool = False,
) -> SecretManager:
    """Get the global SecretManager instance.
    
    Args:
        discord_bot_name: Which Discord bot to use
        force_new: Force creation of a new instance
        
    Returns:
        SecretManager instance
    """
    global _secret_manager
    
    if _secret_manager is None or force_new:
        _secret_manager = SecretManager(discord_bot_name=discord_bot_name)
    
    return _secret_manager


@lru_cache
def get_secrets(discord_bot_name: str = "mr-swede") -> AppSecrets:
    """Convenience function to get all secrets.
    
    This is cached for the lifetime of the application.
    
    Args:
        discord_bot_name: Which Discord bot credentials to load
        
    Returns:
        AppSecrets with all credentials
    """
    manager = get_secret_manager(discord_bot_name=discord_bot_name)
    return manager.get_all_secrets()

