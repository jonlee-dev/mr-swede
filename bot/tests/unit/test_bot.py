"""Unit tests for the Discord bot module."""

from unittest.mock import MagicMock, patch

import pytest

from src.config.secrets import AppSecrets, DiscordBotSecrets
from src.config.settings import Settings


class TestGetBotToken:
    """Tests for get_bot_token function."""
    
    @pytest.fixture
    def mock_settings(self) -> Settings:
        """Create mock settings."""
        return Settings(
            environment="test",
            discord_bot_name="mr-swede",
        )
    
    @pytest.fixture
    def mock_secrets_with_discord(self) -> AppSecrets:
        """Create mock secrets with Discord credentials."""
        return AppSecrets(
            blizzard=None,
            discord=DiscordBotSecrets(
                id="123456789",
                token="gsm-discord-token",
                public_key="public-key",
            ),
            spotify=None,
        )
    
    @pytest.fixture
    def mock_secrets_without_discord(self) -> AppSecrets:
        """Create mock secrets without Discord credentials."""
        return AppSecrets(
            blizzard=None,
            discord=None,
            spotify=None,
        )
    
    def test_get_bot_token_from_secrets(self, mock_settings, mock_secrets_with_discord):
        """Test getting bot token from secrets."""
        from src.bot import get_bot_token
        
        with patch("src.bot.get_settings", return_value=mock_settings), \
             patch("src.bot.get_secrets", return_value=mock_secrets_with_discord):
            
            token = get_bot_token()
            assert token == "gsm-discord-token"
    
    def test_get_bot_token_from_env_fallback(self, mock_settings, mock_secrets_without_discord):
        """Test getting bot token from environment variable fallback."""
        from pydantic import SecretStr
        from src.bot import get_bot_token
        
        mock_settings.discord_token = SecretStr("env-discord-token")
        
        with patch("src.bot.get_settings", return_value=mock_settings), \
             patch("src.bot.get_secrets", return_value=mock_secrets_without_discord):
            
            token = get_bot_token()
            assert token == "env-discord-token"
    
    def test_get_bot_token_raises_without_credentials(self, mock_settings, mock_secrets_without_discord):
        """Test that get_bot_token raises ValueError without credentials."""
        from src.bot import get_bot_token
        
        mock_settings.discord_token = None
        
        with patch("src.bot.get_settings", return_value=mock_settings), \
             patch("src.bot.get_secrets", return_value=mock_secrets_without_discord):
            
            with pytest.raises(ValueError) as exc_info:
                get_bot_token()
            
            assert "No Discord token found" in str(exc_info.value)
            assert "mr-swede" in str(exc_info.value)


class TestMrSwedeBot:
    """Tests for MrSwede bot class."""
    
    @pytest.fixture
    def mock_secrets(self) -> AppSecrets:
        """Create mock secrets."""
        return AppSecrets(
            blizzard=None,
            discord=DiscordBotSecrets(
                id="123456789",
                token="test-token",
                public_key="test-key",
            ),
            spotify=None,
        )
    
    def test_bot_initialization(self, mock_settings, mock_secrets):
        """Test bot initialization."""
        from src.bot import MrSwede
        
        with patch("src.bot.get_settings", return_value=mock_settings), \
             patch("src.bot.get_secrets", return_value=mock_secrets):
            
            bot = MrSwede()
            
            assert bot.settings == mock_settings
            assert bot.secrets == mock_secrets
            assert bot.command_prefix == "$"
    
    def test_bot_has_required_intents(self, mock_settings, mock_secrets):
        """Test that bot has required intents enabled."""
        from src.bot import MrSwede
        
        with patch("src.bot.get_settings", return_value=mock_settings), \
             patch("src.bot.get_secrets", return_value=mock_secrets):
            
            bot = MrSwede()
            
            assert bot.intents.message_content
            assert bot.intents.voice_states
            assert bot.intents.guilds
            assert bot.intents.members

