"""Unit tests for src.config.secrets."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.config.secrets import AppSecrets, DiscordBotSecrets, SecretManager


@pytest.fixture
def manager() -> SecretManager:
    return SecretManager(project_id="test-project", discord_bot_name="mr-swede")


class TestSecretManager:
    def test_default_bot_name(self):
        assert SecretManager()._discord_bot_name == "mr-swede"

    def test_custom_bot_name(self):
        assert SecretManager(discord_bot_name="other-bot")._discord_bot_name == "other-bot"

    def test_secret_path_uses_project_id(self, manager: SecretManager):
        path = manager._get_secret_path("discord")
        assert "test-project" in path
        assert "discord-bot-secrets" in path

    def test_secret_path_env_override(self, manager: SecretManager):
        with patch.dict("os.environ", {"DISCORD_SECRET_PATH": "custom/path"}):
            assert manager._get_secret_path("discord") == "custom/path"

    def test_get_discord_secrets_from_env(self, manager: SecretManager):
        with patch.dict(
            "os.environ",
            {
                "DISCORD_TOKEN": "env-token",
                "DISCORD_APPLICATION_ID": "env-app-id",
                "DISCORD_PUBLIC_KEY": "env-public-key",
            },
        ):
            secrets = manager.get_discord_secrets()

        assert secrets is not None
        assert secrets.token == "env-token"
        assert secrets.id == "env-app-id"
        assert secrets.public_key == "env-public-key"

    def test_get_discord_secrets_dot_notation(self, manager: SecretManager):
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = json.dumps(
            {
                "mr-swede.id": "bot-id",
                "mr-swede.token": "bot-token",
                "mr-swede.public_key": "bot-pk",
            }
        )
        mock_client = MagicMock()
        mock_client.access_secret_version.return_value = mock_response
        manager._client = mock_client

        with patch.dict("os.environ", {}, clear=True):
            secrets = manager.get_discord_secrets("mr-swede")

        assert secrets is not None
        assert secrets.token == "bot-token"
        assert secrets.id == "bot-id"

    def test_get_discord_secrets_nested_object(self, manager: SecretManager):
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = json.dumps(
            {"mr-swede": {"id": "nested-id", "token": "nested-token", "public_key": "k"}}
        )
        mock_client = MagicMock()
        mock_client.access_secret_version.return_value = mock_response
        manager._client = mock_client

        with patch.dict("os.environ", {}, clear=True):
            secrets = manager.get_discord_secrets("mr-swede")

        assert secrets is not None
        assert secrets.token == "nested-token"

    def test_bot_not_found_returns_none(self, manager: SecretManager):
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = json.dumps(
            {"other-bot.token": "irrelevant"}
        )
        mock_client = MagicMock()
        mock_client.access_secret_version.return_value = mock_response
        manager._client = mock_client

        with patch.dict("os.environ", {}, clear=True):
            assert manager.get_discord_secrets("nonexistent-bot") is None

    def test_fetch_caches_results(self, manager: SecretManager):
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = json.dumps({"key": "value"})
        mock_client = MagicMock()
        mock_client.access_secret_version.return_value = mock_response
        manager._client = mock_client

        manager._fetch_secret_json("test/path")
        manager._fetch_secret_json("test/path")

        # Cache hit on second call -- only one network round-trip.
        assert mock_client.access_secret_version.call_count == 1

    def test_clear_cache(self, manager: SecretManager):
        manager._cache["x"] = "y"
        manager.clear_cache()
        assert manager._cache == {}


class TestSecretDataClasses:
    def test_discord_bot_secrets_frozen(self):
        secrets = DiscordBotSecrets(id="i", token="t", public_key="k")
        with pytest.raises(AttributeError):
            secrets.token = "new"

    def test_app_secrets_frozen(self):
        app = AppSecrets(discord=None)
        with pytest.raises(AttributeError):
            app.discord = DiscordBotSecrets(id="i", token="t", public_key="k")
