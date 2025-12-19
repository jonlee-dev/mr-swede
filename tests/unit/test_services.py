"""Unit tests for service clients."""

from unittest.mock import AsyncMock, patch

import pytest

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

