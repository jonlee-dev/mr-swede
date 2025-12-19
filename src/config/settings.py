"""Application settings with Google Secret Manager integration."""

import os
from functools import lru_cache
from typing import Any

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_secret_from_gsm(secret_name: str, project_id: str) -> str | None:
    """Fetch a secret from Google Secret Manager.
    
    Args:
        secret_name: Name of the secret in GSM
        project_id: GCP project ID
        
    Returns:
        The secret value or None if not found/error
    """
    try:
        from google.cloud import secretmanager
        
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except Exception:
        return None


class Settings(BaseSettings):
    """Application settings loaded from environment or Google Secret Manager."""
    
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
    gcp_project_id: str = Field(default="", alias="GCP_PROJECT_ID")
    use_gsm: bool = Field(default=True, description="Use Google Secret Manager for secrets")
    
    # Discord
    discord_token: SecretStr = Field(default=SecretStr(""), alias="DISCORD_TOKEN")
    discord_application_id: str = Field(default="", alias="DISCORD_APPLICATION_ID")
    discord_guild_id: str = Field(default="", alias="DISCORD_GUILD_ID")
    
    # Blizzard/Battle.net API
    blizzard_client_id: str = Field(default="", alias="BLIZZARD_CLIENT_ID")
    blizzard_client_secret: SecretStr = Field(default=SecretStr(""), alias="BLIZZARD_CLIENT_SECRET")
    blizzard_region: str = Field(default="us", alias="BLIZZARD_REGION")
    
    # Spotify API
    spotify_client_id: str = Field(default="", alias="SPOTIFY_CLIENT_ID")
    spotify_client_secret: SecretStr = Field(default=SecretStr(""), alias="SPOTIFY_CLIENT_SECRET")
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
    
    @model_validator(mode="before")
    @classmethod
    def load_secrets_from_gsm(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Load secrets from Google Secret Manager if configured."""
        # Check if we should use GSM
        use_gsm = data.get("use_gsm", data.get("USE_GSM", True))
        gcp_project_id = data.get("gcp_project_id", data.get("GCP_PROJECT_ID", ""))
        
        # In Cloud Run, project ID is available via metadata
        if not gcp_project_id:
            gcp_project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
            if gcp_project_id:
                data["gcp_project_id"] = gcp_project_id
        
        if not use_gsm or not gcp_project_id:
            return data
        
        # Map of settings to GSM secret names
        secret_mappings = {
            "discord_token": "discord-token",
            "blizzard_client_id": "blizzard-client-id",
            "blizzard_client_secret": "blizzard-client-secret",
            "spotify_client_id": "spotify-client-id",
            "spotify_client_secret": "spotify-client-secret",
        }
        
        for setting_key, secret_name in secret_mappings.items():
            # Only fetch from GSM if not already set
            if not data.get(setting_key) and not os.environ.get(setting_key.upper()):
                secret_value = get_secret_from_gsm(secret_name, gcp_project_id)
                if secret_value:
                    data[setting_key] = secret_value
        
        return data
    
    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment.lower() == "production"
    
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

