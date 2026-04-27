"""Unit tests for src.bot."""

from unittest.mock import patch

import pytest
from pydantic import SecretStr

from src.bot import COG_MODULES, MrSwede, get_bot_token
from src.config.secrets import AppSecrets, DiscordBotSecrets
from src.config.settings import Settings


@pytest.fixture
def settings_only() -> Settings:
    return Settings(environment="test", discord_bot_name="mr-swede")


@pytest.fixture
def secrets_with_discord() -> AppSecrets:
    return AppSecrets(
        discord=DiscordBotSecrets(id="123", token="gsm-token", public_key="pk"),
        valheim_password=None,
    )


@pytest.fixture
def secrets_empty() -> AppSecrets:
    return AppSecrets(discord=None, valheim_password=None)


class TestGetBotToken:
    def test_from_secrets(self, settings_only, secrets_with_discord):
        with (
            patch("src.bot.get_settings", return_value=settings_only),
            patch("src.bot.get_secrets", return_value=secrets_with_discord),
        ):
            assert get_bot_token() == "gsm-token"

    def test_env_fallback(self, settings_only, secrets_empty):
        settings_only.discord_token = SecretStr("env-token")
        with (
            patch("src.bot.get_settings", return_value=settings_only),
            patch("src.bot.get_secrets", return_value=secrets_empty),
        ):
            assert get_bot_token() == "env-token"

    def test_raises_without_credentials(self, settings_only, secrets_empty):
        settings_only.discord_token = None
        with (
            patch("src.bot.get_settings", return_value=settings_only),
            patch("src.bot.get_secrets", return_value=secrets_empty),
            pytest.raises(ValueError, match="No Discord token found"),
        ):
            get_bot_token()


class TestMrSwedeBot:
    def test_initialization(self, settings_only, secrets_with_discord):
        with (
            patch("src.bot.get_settings", return_value=settings_only),
            patch("src.bot.get_secrets", return_value=secrets_with_discord),
        ):
            bot = MrSwede()
            assert bot.settings == settings_only
            assert bot.secrets == secrets_with_discord

    def test_uses_default_intents(self, settings_only, secrets_with_discord):
        """Slash-only bot doesn't need privileged intents."""
        with (
            patch("src.bot.get_settings", return_value=settings_only),
            patch("src.bot.get_secrets", return_value=secrets_with_discord),
        ):
            bot = MrSwede()
            # Default intents have guilds on, members/message_content/voice_states off.
            assert bot.intents.guilds
            assert not bot.intents.message_content
            assert not bot.intents.members


def test_cog_modules_list():
    """Sanity check: the cog list points at modules that actually exist."""
    import importlib

    for module_path in COG_MODULES:
        importlib.import_module(module_path)
