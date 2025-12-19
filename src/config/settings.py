"""Application settings with Google Secret Manager integration."""

import os
from functools import lru_cache
from typing import Any

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment.
    
    Note: Secrets are loaded separately via the SecretManager class.
    This class handles non-secret configuration.
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # Environment
    environment: str = Field(default="development", alias="ENV")
    debug: bool = Field(default=False)
    
    # GCP Settings
    gcp_project_id: str = Field(default="749144818572", alias="GCP_PROJECT_ID")
    # Firestore uses project name, not number
    firestore_project: str = Field(default="mr-swede", alias="FIRESTORE_PROJECT")
    
    # Discord bot selection (which bot from discord-bot-secrets to use)
    discord_bot_name: str = Field(
        default="mr-swede",
        alias="DISCORD_BOT_NAME",
        description="Which Discord bot to use: 'mr-swede' or 'ow2-ranked-bot'"
    )
    discord_guild_id: str = Field(default="", alias="DISCORD_GUILD_ID")
    
    # Blizzard API settings
    blizzard_region: str = Field(default="us", alias="BLIZZARD_REGION")
    
    # Spotify settings
    spotify_redirect_uri: str = Field(
        default="http://localhost:8080/callback", 
        alias="SPOTIFY_REDIRECT_URI"
    )
    
    # Firestore
    firestore_collection_prefix: str = Field(
        default="mr_swede_", 
        alias="FIRESTORE_COLLECTION_PREFIX"
    )
    
    # Server settings (for Cloud Run health checks)
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8080, alias="PORT")
    
    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="json", alias="LOG_FORMAT")
    
    # Legacy environment variable support (for local dev without GSM)
    # These are optional and only used if GSM is not available
    discord_token: SecretStr | None = Field(default=None, alias="DISCORD_TOKEN")
    discord_application_id: str | None = Field(default=None, alias="DISCORD_APPLICATION_ID")
    blizzard_client_id: str | None = Field(default=None, alias="BLIZZARD_CLIENT_ID")
    blizzard_client_secret: SecretStr | None = Field(default=None, alias="BLIZZARD_CLIENT_SECRET")
    spotify_client_id: str | None = Field(default=None, alias="SPOTIFY_CLIENT_ID")
    spotify_client_secret: SecretStr | None = Field(default=None, alias="SPOTIFY_CLIENT_SECRET")
    
    @model_validator(mode="before")
    @classmethod
    def detect_gcp_project(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Auto-detect GCP project ID in Cloud Run environment."""
        if not data.get("gcp_project_id") and not os.environ.get("GCP_PROJECT_ID"):
            # Cloud Run sets GOOGLE_CLOUD_PROJECT
            gcp_project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
            if gcp_project:
                data["gcp_project_id"] = gcp_project
        return data
    
    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment.lower() == "production"
    
    @property
    def is_cloud_run(self) -> bool:
        """Check if running in Cloud Run."""
        return bool(os.environ.get("K_SERVICE"))
    
    @property
    def blizzard_token_url(self) -> str:
        """Get the Blizzard OAuth token URL for the configured region."""
        return f"https://{self.blizzard_region}.battle.net/oauth/token"
    
    @property
    def blizzard_api_url(self) -> str:
        """Get the Blizzard API base URL for the configured region."""
        return f"https://{self.blizzard_region}.api.blizzard.com"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
