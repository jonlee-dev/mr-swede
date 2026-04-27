"""Pytest configuration and fixtures."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from discord.ext import commands

from src.config.secrets import AppSecrets, DiscordBotSecrets
from src.config.settings import Settings


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(
        environment="test",
        debug=True,
        gcp_project_id="test-project",
        discord_bot_name="mr-swede",
        discord_guild_id="987654321",
    )


@pytest.fixture
def mock_secrets() -> AppSecrets:
    return AppSecrets(
        discord=DiscordBotSecrets(
            id="123456789",
            token="test-discord-token",
            public_key="test-public-key",
        ),
        valheim_password="test-server-password",
    )


@pytest.fixture
def mock_settings_and_secrets(mock_settings: Settings, mock_secrets: AppSecrets):
    """Patch both get_settings and get_secrets at their definition sites."""
    with (
        patch("src.config.settings.get_settings", return_value=mock_settings),
        patch("src.config.secrets.get_secrets", return_value=mock_secrets),
    ):
        yield mock_settings, mock_secrets


@pytest.fixture
def mock_bot() -> MagicMock:
    bot = MagicMock(spec=commands.Bot)
    bot.latency = 0.05
    bot.guilds = []
    bot.user = MagicMock()
    bot.user.id = 123456789
    bot.is_ready.return_value = True
    return bot


@pytest.fixture
def mock_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = 111222333
    interaction.guild = MagicMock()
    interaction.guild.id = 987654321
    return interaction
