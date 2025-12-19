"""Unit tests for service clients."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.secrets import AppSecrets, BlizzardSecrets, SpotifySecrets
from src.database.models import CompetitiveStats, RankInfo
from src.services.overfast import OverfastClient


class TestOverfastClient:
    """Tests for the Overfast API client."""
    
    def test_normalize_battle_tag_with_hash(self):
        """Test BattleTag normalization with # separator."""
        result = OverfastClient.normalize_battle_tag("Player#1234")
        assert result == "Player-1234"
    
    def test_normalize_battle_tag_already_normalized(self):
        """Test BattleTag already in normalized format."""
        result = OverfastClient.normalize_battle_tag("Player-1234")
        assert result == "Player-1234"
    
    @pytest.mark.asyncio
    async def test_get_player_summary_success(self, overfast_player_summary_response):
        """Test successful player summary fetch."""
        client = OverfastClient()
        
        with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = overfast_player_summary_response
            
            result = await client.get_player_summary("TestPlayer#1234")
            
            mock_get.assert_called_once_with("/players/TestPlayer-1234/summary")
            assert result["username"] == "TestPlayer"
    
    @pytest.mark.asyncio
    async def test_get_competitive_stats_success(self, overfast_player_summary_response):
        """Test parsing competitive stats from API response."""
        client = OverfastClient()
        
        with patch.object(client, "get_player_summary", new_callable=AsyncMock) as mock_summary:
            mock_summary.return_value = overfast_player_summary_response
            
            stats = await client.get_competitive_stats("TestPlayer#1234")
            
            assert isinstance(stats, CompetitiveStats)
            assert stats.tank.division == "Diamond"
            assert stats.damage.division == "Master"
            assert stats.support.division == "Grandmaster"
    
    @pytest.mark.asyncio
    async def test_get_competitive_stats_no_data(self):
        """Test handling player with no competitive data."""
        client = OverfastClient()
        
        with patch.object(client, "get_player_summary", new_callable=AsyncMock) as mock_summary:
            mock_summary.return_value = {"username": "Player", "competitive": {}}
            
            stats = await client.get_competitive_stats("Player#1234")
            
            assert stats.tank.division == ""
            assert stats.damage.division == ""
            assert stats.support.division == ""
    
    @pytest.mark.asyncio
    async def test_search_players(self):
        """Test player search."""
        client = OverfastClient()
        
        mock_response = {"results": [{"player_id": "Player-1234", "name": "Player"}]}
        
        with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            
            results = await client.search_players("Player", limit=10)
            
            mock_get.assert_called_once_with("/players", params={"name": "Player", "limit": 10})
            assert len(results) == 1


class TestBlizzardClient:
    """Tests for the Blizzard API client."""
    
    @pytest.fixture
    def mock_secrets(self) -> AppSecrets:
        """Create mock secrets."""
        return AppSecrets(
            blizzard=BlizzardSecrets(
                client_id="test-client-id",
                client_secret="test-client-secret",
            ),
            discord=None,
            spotify=None,
        )
    
    def test_validate_battle_tag_format_valid(self):
        """Test valid BattleTag formats."""
        from src.services.blizzard import BlizzardClient
        
        with patch("src.services.blizzard.get_secrets") as mock_get_secrets:
            mock_get_secrets.return_value = AppSecrets(
                blizzard=BlizzardSecrets("id", "secret"),
                discord=None,
                spotify=None,
            )
            
            client = BlizzardClient()
            
            # Run the sync validation method
            import asyncio
            loop = asyncio.new_event_loop()
            
            assert loop.run_until_complete(client.validate_battle_tag_format("Player#1234"))
            assert loop.run_until_complete(client.validate_battle_tag_format("Ab#12345678"))
            assert not loop.run_until_complete(client.validate_battle_tag_format("Invalid"))
            assert not loop.run_until_complete(client.validate_battle_tag_format("Player#123"))  # Too short
            
            loop.close()


class TestSpotifyClient:
    """Tests for the Spotify client."""
    
    @pytest.fixture
    def mock_secrets(self) -> AppSecrets:
        """Create mock secrets."""
        return AppSecrets(
            blizzard=None,
            discord=None,
            spotify=SpotifySecrets(
                client_id="test-spotify-id",
                client_secret="test-spotify-secret",
            ),
        )
    
    def test_parse_spotify_url_track(self, mock_secrets):
        """Test parsing Spotify track URL."""
        from src.services.spotify import SpotifyClient
        
        with patch("src.services.spotify.get_secrets", return_value=mock_secrets):
            client = SpotifyClient()
            
            result = client.parse_spotify_url("https://open.spotify.com/track/abc123")
            assert result == ("track", "abc123")
    
    def test_parse_spotify_url_playlist(self, mock_secrets):
        """Test parsing Spotify playlist URL."""
        from src.services.spotify import SpotifyClient
        
        with patch("src.services.spotify.get_secrets", return_value=mock_secrets):
            client = SpotifyClient()
            
            result = client.parse_spotify_url("https://open.spotify.com/playlist/xyz789")
            assert result == ("playlist", "xyz789")
    
    def test_parse_spotify_uri(self, mock_secrets):
        """Test parsing Spotify URI."""
        from src.services.spotify import SpotifyClient
        
        with patch("src.services.spotify.get_secrets", return_value=mock_secrets):
            client = SpotifyClient()
            
            result = client.parse_spotify_url("spotify:track:abc123")
            assert result == ("track", "abc123")
    
    def test_parse_spotify_url_invalid(self, mock_secrets):
        """Test parsing invalid Spotify URL."""
        from src.services.spotify import SpotifyClient
        
        with patch("src.services.spotify.get_secrets", return_value=mock_secrets):
            client = SpotifyClient()
            
            result = client.parse_spotify_url("https://youtube.com/watch?v=abc123")
            assert result is None


class TestCompetitiveStats:
    """Tests for CompetitiveStats model."""
    
    def test_get_highest_rank_all_ranked(self):
        """Test getting highest rank when all roles are ranked."""
        stats = CompetitiveStats(
            tank=RankInfo(division="Gold", tier=2),
            damage=RankInfo(division="Diamond", tier=4),
            support=RankInfo(division="Platinum", tier=1),
        )
        
        highest = stats.get_highest_rank()
        
        assert highest.division == "Diamond"
        assert highest.tier == 4
    
    def test_get_highest_rank_same_division(self):
        """Test getting highest rank when multiple roles have same division."""
        stats = CompetitiveStats(
            tank=RankInfo(division="Diamond", tier=3),
            damage=RankInfo(division="Diamond", tier=1),
            support=RankInfo(division="Diamond", tier=5),
        )
        
        highest = stats.get_highest_rank()
        
        assert highest.division == "Diamond"
        assert highest.tier == 1  # Tier 1 is best
    
    def test_get_highest_rank_no_ranks(self):
        """Test getting highest rank when no roles are ranked."""
        stats = CompetitiveStats()
        
        highest = stats.get_highest_rank()
        
        assert highest.division == ""
    
    def test_get_highest_rank_partial(self):
        """Test getting highest rank when only some roles are ranked."""
        stats = CompetitiveStats(
            tank=RankInfo(division="Gold", tier=3),
            damage=RankInfo(),  # Unranked
            support=RankInfo(),  # Unranked
        )
        
        highest = stats.get_highest_rank()
        
        assert highest.division == "Gold"


class TestRankInfo:
    """Tests for RankInfo model."""
    
    def test_display_with_rank(self):
        """Test display property with rank."""
        rank = RankInfo(division="Master", tier=2)
        assert rank.display == "Master 2"
    
    def test_display_unranked(self):
        """Test display property when unranked."""
        rank = RankInfo()
        assert rank.display == "Unranked"
