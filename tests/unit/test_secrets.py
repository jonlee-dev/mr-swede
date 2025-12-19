"""Unit tests for the secrets module."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.config.secrets import (
    AppSecrets,
    BlizzardSecrets,
    DiscordBotSecrets,
    SecretManager,
    SpotifySecrets,
)


class TestSecretManager:
    """Tests for SecretManager class."""
    
    @pytest.fixture
    def secret_manager(self) -> SecretManager:
        """Create a SecretManager instance."""
        return SecretManager(
            project_id="test-project",
            discord_bot_name="mr-swede",
        )
    
    def test_init_default_values(self):
        """Test initialization with default values."""
        manager = SecretManager()
        assert manager._discord_bot_name == "mr-swede"
    
    def test_init_custom_bot_name(self):
        """Test initialization with custom bot name."""
        manager = SecretManager(discord_bot_name="ow2-ranked-bot")
        assert manager._discord_bot_name == "ow2-ranked-bot"
    
    def test_get_secret_path_default(self, secret_manager: SecretManager):
        """Test getting default secret path."""
        path = secret_manager._get_secret_path("discord")
        assert "discord-bot-secrets" in path
    
    def test_get_secret_path_env_override(self, secret_manager: SecretManager):
        """Test secret path override via environment variable."""
        with patch.dict("os.environ", {"DISCORD_SECRET_PATH": "custom/path"}):
            path = secret_manager._get_secret_path("discord")
            assert path == "custom/path"
    
    def test_get_blizzard_secrets_from_env(self, secret_manager: SecretManager):
        """Test loading Blizzard secrets from environment."""
        with patch.dict("os.environ", {
            "BLIZZARD_CLIENT_ID": "env-client-id",
            "BLIZZARD_CLIENT_SECRET": "env-client-secret",
        }):
            secrets = secret_manager.get_blizzard_secrets()
            
            assert secrets is not None
            assert secrets.client_id == "env-client-id"
            assert secrets.client_secret == "env-client-secret"
    
    def test_get_discord_secrets_from_env(self, secret_manager: SecretManager):
        """Test loading Discord secrets from environment."""
        with patch.dict("os.environ", {
            "DISCORD_TOKEN": "env-token",
            "DISCORD_APPLICATION_ID": "env-app-id",
            "DISCORD_PUBLIC_KEY": "env-public-key",
        }):
            secrets = secret_manager.get_discord_secrets()
            
            assert secrets is not None
            assert secrets.token == "env-token"
            assert secrets.id == "env-app-id"
            assert secrets.public_key == "env-public-key"
    
    def test_get_spotify_secrets_from_env(self, secret_manager: SecretManager):
        """Test loading Spotify secrets from environment."""
        with patch.dict("os.environ", {
            "SPOTIFY_CLIENT_ID": "env-spotify-id",
            "SPOTIFY_CLIENT_SECRET": "env-spotify-secret",
        }):
            secrets = secret_manager.get_spotify_secrets()
            
            assert secrets is not None
            assert secrets.client_id == "env-spotify-id"
            assert secrets.client_secret == "env-spotify-secret"
    
    def test_get_blizzard_secrets_from_gsm(self, secret_manager: SecretManager):
        """Test loading Blizzard secrets from GSM."""
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = json.dumps({
            "client_id": "gsm-client-id",
            "client_secret": "gsm-client-secret",
        })
        
        mock_client = MagicMock()
        mock_client.access_secret_version.return_value = mock_response
        
        # Patch the _client attribute directly
        secret_manager._client = mock_client
        
        with patch.dict("os.environ", {}, clear=True):
            secrets = secret_manager.get_blizzard_secrets()
            
            assert secrets is not None
            assert secrets.client_id == "gsm-client-id"
            assert secrets.client_secret == "gsm-client-secret"
    
    def test_get_discord_secrets_dot_notation(self, secret_manager: SecretManager):
        """Test loading Discord secrets with dot notation keys."""
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = json.dumps({
            "mr-swede.id": "bot-id",
            "mr-swede.token": "bot-token",
            "mr-swede.public_key": "bot-public-key",
        })
        
        mock_client = MagicMock()
        mock_client.access_secret_version.return_value = mock_response
        
        # Patch the _client attribute directly
        secret_manager._client = mock_client
        
        with patch.dict("os.environ", {}, clear=True):
            secrets = secret_manager.get_discord_secrets("mr-swede")
            
            assert secrets is not None
            assert secrets.token == "bot-token"
            assert secrets.id == "bot-id"
    
    def test_get_discord_secrets_nested_object(self, secret_manager: SecretManager):
        """Test loading Discord secrets with nested object structure."""
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = json.dumps({
            "mr-swede": {
                "id": "nested-id",
                "token": "nested-token",
                "public_key": "nested-key",
            }
        })
        
        mock_client = MagicMock()
        mock_client.access_secret_version.return_value = mock_response
        
        # Patch the _client attribute directly
        secret_manager._client = mock_client
        
        with patch.dict("os.environ", {}, clear=True):
            secrets = secret_manager.get_discord_secrets("mr-swede")
            
            assert secrets is not None
            assert secrets.token == "nested-token"
    
    def test_get_all_secrets(self, secret_manager: SecretManager):
        """Test getting all secrets at once."""
        with patch.dict("os.environ", {
            "DISCORD_TOKEN": "test-token",
            "BLIZZARD_CLIENT_ID": "blizzard-id",
            "BLIZZARD_CLIENT_SECRET": "blizzard-secret",
            "SPOTIFY_CLIENT_ID": "spotify-id",
            "SPOTIFY_CLIENT_SECRET": "spotify-secret",
        }):
            secrets = secret_manager.get_all_secrets()
            
            assert isinstance(secrets, AppSecrets)
            assert secrets.discord is not None
            assert secrets.blizzard is not None
            assert secrets.spotify is not None
    
    def test_cache_clearing(self, secret_manager: SecretManager):
        """Test cache clearing functionality."""
        secret_manager._cache["test"] = "value"
        assert len(secret_manager._cache) == 1
        
        secret_manager.clear_cache()
        assert len(secret_manager._cache) == 0
    
    def test_get_discord_secrets_bot_not_found(self, secret_manager: SecretManager):
        """Test handling when bot name is not found in secrets."""
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = json.dumps({
            "other-bot.token": "some-token",
        })
        
        mock_client = MagicMock()
        mock_client.access_secret_version.return_value = mock_response
        
        # Patch the _client attribute directly
        secret_manager._client = mock_client
        
        with patch.dict("os.environ", {}, clear=True):
            secrets = secret_manager.get_discord_secrets("nonexistent-bot")
            
            assert secrets is None
    
    def test_fetch_secret_json_caches_result(self, secret_manager: SecretManager):
        """Test that fetched secrets are cached."""
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = json.dumps({"key": "value"})
        
        mock_client = MagicMock()
        mock_client.access_secret_version.return_value = mock_response
        
        secret_manager._client = mock_client
        
        secret_path = "test/path"
        
        # First call
        result1 = secret_manager._fetch_secret_json(secret_path)
        # Second call (should use cache)
        result2 = secret_manager._fetch_secret_json(secret_path)
        
        # Should only call GSM once
        assert mock_client.access_secret_version.call_count == 1
        assert result1 == result2
        assert result1 == {"key": "value"}


class TestSecretDataClasses:
    """Tests for secret data classes."""
    
    def test_blizzard_secrets_frozen(self):
        """Test that BlizzardSecrets is immutable."""
        secrets = BlizzardSecrets(
            client_id="id",
            client_secret="secret",
        )
        
        with pytest.raises(AttributeError):
            secrets.client_id = "new-id"
    
    def test_discord_bot_secrets_frozen(self):
        """Test that DiscordBotSecrets is immutable."""
        secrets = DiscordBotSecrets(
            id="id",
            token="token",
            public_key="key",
        )
        
        with pytest.raises(AttributeError):
            secrets.token = "new-token"
    
    def test_spotify_secrets_frozen(self):
        """Test that SpotifySecrets is immutable."""
        secrets = SpotifySecrets(
            client_id="id",
            client_secret="secret",
        )
        
        with pytest.raises(AttributeError):
            secrets.client_id = "new-id"
    
    def test_app_secrets_frozen(self):
        """Test that AppSecrets is immutable."""
        app_secrets = AppSecrets(
            blizzard=None,
            discord=None,
            spotify=None,
        )
        
        with pytest.raises(AttributeError):
            app_secrets.blizzard = BlizzardSecrets("id", "secret")
    
    def test_blizzard_secrets_equality(self):
        """Test BlizzardSecrets equality comparison."""
        secrets1 = BlizzardSecrets(client_id="id", client_secret="secret")
        secrets2 = BlizzardSecrets(client_id="id", client_secret="secret")
        secrets3 = BlizzardSecrets(client_id="different", client_secret="secret")
        
        assert secrets1 == secrets2
        assert secrets1 != secrets3
    
    def test_discord_bot_secrets_creation(self):
        """Test DiscordBotSecrets creation."""
        secrets = DiscordBotSecrets(
            id="123456789",
            token="super-secret-token",
            public_key="public-key-here",
        )
        
        assert secrets.id == "123456789"
        assert secrets.token == "super-secret-token"
        assert secrets.public_key == "public-key-here"
