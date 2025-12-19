"""Pytest configuration and fixtures."""

import asyncio
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from discord.ext import commands

from src.config.settings import Settings
from src.database.models import Account, CompetitiveStats, RankInfo


# ==================== Pytest Configuration ====================

@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ==================== Settings Fixtures ====================

@pytest.fixture
def mock_settings() -> Settings:
    """Create mock settings for testing."""
    return Settings(
        environment="test",
        debug=True,
        gcp_project_id="test-project",
        use_gsm=False,
        discord_token="test-token",
        discord_application_id="123456789",
        discord_guild_id="987654321",
        blizzard_client_id="test-blizzard-id",
        blizzard_client_secret="test-blizzard-secret",
        spotify_client_id="test-spotify-id",
        spotify_client_secret="test-spotify-secret",
    )


@pytest.fixture
def mock_settings_patch(mock_settings: Settings):
    """Patch get_settings to return mock settings."""
    with patch("src.config.settings.get_settings", return_value=mock_settings):
        yield mock_settings


# ==================== Discord Fixtures ====================

@pytest.fixture
def mock_bot() -> MagicMock:
    """Create a mock Discord bot."""
    bot = MagicMock(spec=commands.Bot)
    bot.latency = 0.05
    bot.guilds = []
    bot.user = MagicMock()
    bot.user.id = 123456789
    bot.is_ready.return_value = True
    return bot


@pytest.fixture
def mock_interaction() -> MagicMock:
    """Create a mock Discord interaction."""
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = 111222333
    interaction.user.voice = MagicMock()
    interaction.user.voice.channel = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = 987654321
    interaction.guild.voice_client = None
    return interaction


@pytest.fixture
def mock_voice_client() -> MagicMock:
    """Create a mock voice client."""
    voice_client = MagicMock()
    voice_client.is_playing.return_value = False
    voice_client.is_paused.return_value = False
    voice_client.is_connected.return_value = True
    voice_client.channel = MagicMock()
    voice_client.play = MagicMock()
    voice_client.stop = MagicMock()
    voice_client.pause = MagicMock()
    voice_client.resume = MagicMock()
    return voice_client


# ==================== Data Model Fixtures ====================

@pytest.fixture
def sample_rank_info() -> RankInfo:
    """Create sample rank info."""
    return RankInfo(
        division="Diamond",
        tier=3,
        skill_rating=2850,
    )


@pytest.fixture
def sample_competitive_stats() -> CompetitiveStats:
    """Create sample competitive stats."""
    return CompetitiveStats(
        tank=RankInfo(division="Platinum", tier=2),
        damage=RankInfo(division="Diamond", tier=4),
        support=RankInfo(division="Master", tier=5),
        season=12,
    )


@pytest.fixture
def sample_account(sample_competitive_stats: CompetitiveStats) -> Account:
    """Create sample account."""
    return Account(
        id="test-account-id",
        battle_tag="TestPlayer#1234",
        discord_user_id="111222333",
        display_name="TestPlayer",
        is_main=True,
        platform="pc",
        region="us",
        current_stats=sample_competitive_stats,
    )


# ==================== API Response Fixtures ====================

@pytest.fixture
def overfast_player_summary_response() -> dict:
    """Sample Overfast API player summary response."""
    return {
        "username": "TestPlayer",
        "avatar": "https://example.com/avatar.png",
        "title": "Champion",
        "endorsement": {"level": 3, "frame": "https://example.com/frame.png"},
        "competitive": {
            "pc": {
                "season": {
                    "tank": {"division": "Diamond", "tier": 3},
                    "damage": {"division": "Master", "tier": 2},
                    "support": {"division": "Grandmaster", "tier": 1},
                }
            }
        },
    }


@pytest.fixture
def youtube_search_response() -> dict:
    """Sample YouTube search response."""
    return {
        "entries": [
            {
                "id": "abc123",
                "title": "Test Song - Artist",
                "duration": 240,
                "url": "https://www.youtube.com/watch?v=abc123",
                "thumbnail": "https://example.com/thumb.jpg",
                "uploader": "Test Artist",
            }
        ]
    }


# ==================== Database Fixtures ====================

@pytest.fixture
def mock_firestore_client() -> AsyncMock:
    """Create a mock Firestore client."""
    client = AsyncMock()
    client.get_account = AsyncMock(return_value=None)
    client.get_account_by_battle_tag = AsyncMock(return_value=None)
    client.get_accounts_by_discord_user = AsyncMock(return_value=[])
    client.create_account = AsyncMock(return_value="new-account-id")
    client.update_account = AsyncMock()
    client.update_account_stats = AsyncMock()
    client.delete_account = AsyncMock()
    client.add_stats_history = AsyncMock(return_value="history-id")
    client.get_stats_history = AsyncMock(return_value=[])
    client.get_all_accounts = AsyncMock(return_value=[])
    return client


# ==================== HTTP Client Fixtures ====================

@pytest.fixture
def mock_httpx_client() -> AsyncMock:
    """Create a mock httpx async client."""
    client = AsyncMock()
    client.get = AsyncMock()
    client.post = AsyncMock()
    client.is_closed = False
    return client

