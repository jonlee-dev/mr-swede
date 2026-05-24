"""Application settings loaded from environment + .env."""

import os
from functools import lru_cache
from typing import Any

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Non-secret configuration. Secrets are loaded by SecretManager."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Environment
    environment: str = Field(default="development", alias="ENV")
    debug: bool = Field(default=False)

    # GCP
    gcp_project_id: str = Field(default="", alias="GCP_PROJECT_ID")

    # Discord bot selection
    discord_bot_name: str = Field(
        default="mr-swede",
        alias="DISCORD_BOT_NAME",
        description="Key into discord-bot-secrets JSON identifying which bot to run.",
    )
    discord_guild_id: str = Field(
        default="",
        alias="DISCORD_GUILD_ID",
        description="If set, slash commands sync to this guild only (instant). "
        "If empty, syncs globally (~1hr propagation).",
    )

    # Valheim VM target -- consumed by src.services.compute and the
    # status-fetch HTTP call.
    valheim_zone: str = Field(default="us-central1-a", alias="VALHEIM_ZONE")
    valheim_instance_name: str = Field(default="valheim-server", alias="VALHEIM_INSTANCE_NAME")
    valheim_status_http_port: int = Field(
        default=9001,
        alias="VALHEIM_STATUS_HTTP_PORT",
        description="TCP port on the Valheim VM where the log-scraping status server listens. Must match server/scripts/status-server.py + the firewall rule in gcp-valheim-vm.",
    )

    # Lavalink target. Always localhost on bot-vm (co-tenanted); override
    # for local dev when running Lavalink at a different address.
    lavalink_host: str = Field(
        default="localhost",
        alias="LAVALINK_HOST",
        description="Hostname/IP of the Lavalink server. Defaults to localhost (matches bot-vm topology where Lavalink runs as a sibling systemd service).",
    )
    lavalink_port: int = Field(default=2333, alias="LAVALINK_PORT")

    # Music command channel scope. Empty = no restriction (commands work
    # anywhere); set to a Discord channel ID (string) to scope /music *
    # to that channel and ephemeral-redirect everywhere else.
    music_command_channel_id: str = Field(
        default="",
        alias="MUSIC_COMMAND_CHANNEL_ID",
        description="Discord channel ID where /music * commands are accepted. Empty = no restriction. The decorator in src.utils.checks.requires_channel reads this.",
    )

    # HTTP server (Cloud Run health checks)
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8080, alias="PORT")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="json", alias="LOG_FORMAT")

    # Local-dev fallback for the Discord token. In Cloud Run we always go
    # through GSM; this exists so devs can `export DISCORD_TOKEN=...` and run.
    discord_token: SecretStr | None = Field(default=None, alias="DISCORD_TOKEN")
    discord_application_id: str | None = Field(default=None, alias="DISCORD_APPLICATION_ID")

    # Local-dev fallback for the Lavalink password. Production reads
    # via VALHEIM_PASSWORD_SECRET_PATH; this is for `poetry run python
    # -m src.main` against a localhost Lavalink.
    lavalink_password: SecretStr | None = Field(default=None, alias="LAVALINK_PASSWORD")

    @model_validator(mode="before")
    @classmethod
    def detect_gcp_project(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Auto-detect GCP project ID in Cloud Run.

        Cloud Run sets GOOGLE_CLOUD_PROJECT; we mirror it into our setting if
        the user didn't set GCP_PROJECT_ID explicitly.
        """
        if not data.get("gcp_project_id") and not os.environ.get("GCP_PROJECT_ID"):
            gcp_project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
            if gcp_project:
                data["gcp_project_id"] = gcp_project
        return data

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def is_cloud_run(self) -> bool:
        return bool(os.environ.get("K_SERVICE"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
