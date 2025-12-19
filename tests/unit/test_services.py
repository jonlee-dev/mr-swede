"""Unit tests for service clients."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.secrets import AppSecrets, BlizzardSecrets, SpotifySecrets
from src.database.models import CompetitiveStats, RankInfo
from src.services.overfast import OverfastClient, _wait_for_rate_limit, RATE_LIMIT_INTERVAL
from src.services import overfast as overfast_module


class TestOverfastRateLimiting:
    """Tests for Overfast API rate limiting."""
    
    @pytest.fixture(autouse=True)
    def reset_rate_limit(self):
        """Reset rate limit state before each test."""
        overfast_module._last_request_time = 0
        yield
        overfast_module._last_request_time = 0
    
    @pytest.mark.asyncio
    async def test_rate_limit_first_request_no_wait(self):
        """First request should not wait."""
        overfast_module._last_request_time = 0
        
        start = time.time()
        await _wait_for_rate_limit()
        elapsed = time.time() - start
        
        # Should be nearly instant (< 0.1s)
        assert elapsed < 0.1
    
    @pytest.mark.asyncio
    async def test_rate_limit_waits_when_too_fast(self):
        """Should wait if last request was too recent."""
        # Simulate a recent request
        overfast_module._last_request_time = time.time()
        
        start = time.time()
        await _wait_for_rate_limit()
        elapsed = time.time() - start
        
        # Should wait approximately RATE_LIMIT_INTERVAL
        assert elapsed >= RATE_LIMIT_INTERVAL - 0.1
        assert elapsed < RATE_LIMIT_INTERVAL + 0.5
    
    @pytest.mark.asyncio
    async def test_rate_limit_no_wait_after_interval(self):
        """Should not wait if enough time has passed."""
        # Simulate a request from 2 seconds ago
        overfast_module._last_request_time = time.time() - RATE_LIMIT_INTERVAL - 1
        
        start = time.time()
        await _wait_for_rate_limit()
        elapsed = time.time() - start
        
        # Should be nearly instant
        assert elapsed < 0.1
    
    @pytest.mark.asyncio
    async def test_rate_limit_updates_last_request_time(self):
        """Should update last request time after waiting."""
        overfast_module._last_request_time = 0
        
        before = time.time()
        await _wait_for_rate_limit()
        after = time.time()
        
        # Last request time should be updated
        assert overfast_module._last_request_time >= before
        assert overfast_module._last_request_time <= after


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
        
        with patch.object(client, "_get", new_callable=AsyncMock) as mock_get, \
             patch("src.services.overfast._wait_for_rate_limit", new_callable=AsyncMock):
            mock_get.return_value = overfast_player_summary_response
            
            result = await client.get_player_summary("TestPlayer#1234")
            
            mock_get.assert_called_once_with("/players/TestPlayer-1234/summary")
            assert result["username"] == "TestPlayer"
    
    @pytest.mark.asyncio
    async def test_get_player_summary_calls_rate_limit(self, overfast_player_summary_response):
        """Test that get_player_summary calls rate limiter."""
        client = OverfastClient()
        
        with patch.object(client, "_get", new_callable=AsyncMock) as mock_get, \
             patch("src.services.overfast._wait_for_rate_limit", new_callable=AsyncMock) as mock_rate_limit:
            mock_get.return_value = overfast_player_summary_response
            
            await client.get_player_summary("TestPlayer#1234")
            
            mock_rate_limit.assert_called_once()
    
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
        
        with patch.object(client, "_get", new_callable=AsyncMock) as mock_get, \
             patch("src.services.overfast._wait_for_rate_limit", new_callable=AsyncMock):
            mock_get.return_value = mock_response
            
            results = await client.search_players("Player", limit=10)
            
            mock_get.assert_called_once_with("/players", params={"name": "Player", "limit": 10})
            assert len(results) == 1
    
    @pytest.mark.asyncio
    async def test_search_players_calls_rate_limit(self):
        """Test that search_players calls rate limiter."""
        client = OverfastClient()
        
        with patch.object(client, "_get", new_callable=AsyncMock) as mock_get, \
             patch("src.services.overfast._wait_for_rate_limit", new_callable=AsyncMock) as mock_rate_limit:
            mock_get.return_value = {"results": []}
            
            await client.search_players("Player")
            
            mock_rate_limit.assert_called_once()


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


class TestYouTubeAudioClient:
    """Tests for YouTubeAudioClient."""
    
    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset audio URL cache before each test."""
        from src.services import youtube as youtube_module
        youtube_module._audio_url_cache.clear()
        yield
        youtube_module._audio_url_cache.clear()
    
    def test_build_options_prefers_low_quality(self):
        """Test that _build_options prefers low quality formats."""
        from src.services.youtube import YouTubeAudioClient
        
        with patch("src.services.youtube._get_cookies_path_sync", return_value=None):
            client = YouTubeAudioClient()
            options = client._build_options()
            
            # Should prefer worstaudio (lowest quality) for faster extraction
            assert "worstaudio" in options["format"]
            # Should include opus/vorbis preferences
            assert "opus" in options["format"]
            assert "vorbis" in options["format"]
    
    def test_build_options_disables_retries(self):
        """Test that _build_options disables all retries."""
        from src.services.youtube import YouTubeAudioClient
        
        with patch("src.services.youtube._get_cookies_path_sync", return_value=None):
            client = YouTubeAudioClient()
            options = client._build_options()
            
            assert options["retries"] == 0
            assert options["fragment_retries"] == 0
            assert options["extractor_retries"] == 0
            assert options["file_access_retries"] == 0
    
    def test_build_options_skips_unnecessary_processing(self):
        """Test that _build_options skips DASH/HLS manifests."""
        from src.services.youtube import YouTubeAudioClient
        
        with patch("src.services.youtube._get_cookies_path_sync", return_value=None):
            client = YouTubeAudioClient()
            options = client._build_options()
            
            assert options["skip_download"] is True
            assert options["no_check_formats"] is True
            assert options["youtube_include_dash_manifest"] is False
            assert options["youtube_include_hls_manifest"] is False
    
    def test_get_cache_key_normalizes_youtube_url(self):
        """Test cache key normalization for YouTube URLs."""
        from src.services.youtube import YouTubeAudioClient
        
        client = YouTubeAudioClient()
        
        # Different URL formats should produce same cache key
        key1 = client._get_cache_key("https://www.youtube.com/watch?v=abc123def45")
        key2 = client._get_cache_key("https://youtu.be/abc123def45")
        key3 = client._get_cache_key("https://youtube.com/watch?v=abc123def45&t=120")
        
        assert key1 == "yt:abc123def45"
        assert key2 == "yt:abc123def45"
        assert key3 == "yt:abc123def45"
    
    def test_get_cache_key_lowercase_query(self):
        """Test cache key for search queries is lowercase."""
        from src.services.youtube import YouTubeAudioClient
        
        client = YouTubeAudioClient()
        
        key1 = client._get_cache_key("Baby Shark")
        key2 = client._get_cache_key("baby shark")
        key3 = client._get_cache_key("  BABY SHARK  ")
        
        assert key1 == "baby shark"
        assert key2 == "baby shark"
        assert key3 == "baby shark"
    
    def test_cache_track_stores_and_retrieves(self):
        """Test caching and retrieving audio tracks."""
        from src.services.youtube import YouTubeAudioClient, AudioTrack
        
        client = YouTubeAudioClient()
        
        track = AudioTrack(
            url="https://example.com/audio.opus",
            title="Test Song",
            duration=180,
            webpage_url="https://youtube.com/watch?v=abc123def45",
        )
        
        # Cache the track
        client._cache_track("test query", track)
        
        # Should be retrievable
        cached = client._get_cached_track("test query")
        assert cached is not None
        assert cached.title == "Test Song"
        assert cached.url == track.url
    
    def test_cache_track_also_caches_by_webpage_url(self):
        """Test that caching also stores by webpage URL."""
        from src.services.youtube import YouTubeAudioClient, AudioTrack
        
        client = YouTubeAudioClient()
        
        track = AudioTrack(
            url="https://example.com/audio.opus",
            title="Test Song",
            duration=180,
            webpage_url="https://youtube.com/watch?v=abc123def45",
        )
        
        # Cache by query
        client._cache_track("test query", track)
        
        # Should also be retrievable by webpage URL
        cached = client._get_cached_track("https://youtube.com/watch?v=abc123def45")
        assert cached is not None
        assert cached.title == "Test Song"
    
    def test_cache_returns_none_when_expired(self):
        """Test that expired cache entries return None."""
        from src.services.youtube import YouTubeAudioClient, AudioTrack, AUDIO_URL_CACHE_TTL
        from src.services import youtube as youtube_module
        
        client = YouTubeAudioClient()
        
        track = AudioTrack(
            url="https://example.com/audio.opus",
            title="Test Song",
            duration=180,
        )
        
        # Manually add expired entry
        cache_key = client._get_cache_key("test query")
        expired_time = time.time() - AUDIO_URL_CACHE_TTL - 100
        youtube_module._audio_url_cache[cache_key] = (track, expired_time)
        
        # Should return None for expired
        cached = client._get_cached_track("test query")
        assert cached is None
    
    def test_cache_miss_returns_none(self):
        """Test that cache miss returns None."""
        from src.services.youtube import YouTubeAudioClient
        
        client = YouTubeAudioClient()
        
        cached = client._get_cached_track("nonexistent query")
        assert cached is None
    
    def test_is_youtube_url(self):
        """Test YouTube URL detection."""
        from src.services.youtube import YouTubeAudioClient
        
        client = YouTubeAudioClient()
        
        assert client.is_youtube_url("https://www.youtube.com/watch?v=abc123")
        assert client.is_youtube_url("https://youtu.be/abc123")
        assert client.is_youtube_url("https://music.youtube.com/watch?v=abc123")
        assert client.is_youtube_url("https://youtube.com/playlist?list=abc123")
        
        assert not client.is_youtube_url("https://soundcloud.com/artist/song")
        assert not client.is_youtube_url("https://spotify.com/track/abc123")
        assert not client.is_youtube_url("baby shark")
    
    def test_is_playlist_url(self):
        """Test playlist URL detection."""
        from src.services.youtube import YouTubeAudioClient
        
        client = YouTubeAudioClient()
        
        assert client.is_playlist_url("https://youtube.com/playlist?list=abc123")
        assert client.is_playlist_url("https://youtube.com/watch?v=abc&list=xyz")
        
        assert not client.is_playlist_url("https://youtube.com/watch?v=abc123")
        assert not client.is_playlist_url("baby shark")


class TestAudioTrack:
    """Tests for AudioTrack dataclass."""
    
    def test_duration_str_minutes_seconds(self):
        """Test duration formatting for minutes and seconds."""
        from src.services.youtube import AudioTrack
        
        track = AudioTrack(url="", title="Test", duration=185)
        assert track.duration_str == "3:05"
    
    def test_duration_str_hours(self):
        """Test duration formatting with hours."""
        from src.services.youtube import AudioTrack
        
        track = AudioTrack(url="", title="Test", duration=3725)  # 1:02:05
        assert track.duration_str == "1:02:05"
    
    def test_duration_str_zero(self):
        """Test duration formatting for zero."""
        from src.services.youtube import AudioTrack
        
        track = AudioTrack(url="", title="Test", duration=0)
        assert track.duration_str == "0:00"


class TestThreadPoolExecutors:
    """Tests for thread pool executors."""
    
    def test_get_ffmpeg_executor_returns_executor(self):
        """Test that get_ffmpeg_executor returns a ThreadPoolExecutor."""
        from concurrent.futures import ThreadPoolExecutor
        from src.services.youtube import get_ffmpeg_executor
        
        executor = get_ffmpeg_executor()
        assert isinstance(executor, ThreadPoolExecutor)
    
    def test_get_ytdl_executor_returns_executor(self):
        """Test that get_ytdl_executor returns a ThreadPoolExecutor."""
        from concurrent.futures import ThreadPoolExecutor
        from src.services.youtube import get_ytdl_executor
        
        executor = get_ytdl_executor()
        assert isinstance(executor, ThreadPoolExecutor)
    
    def test_executors_are_different(self):
        """Test that FFmpeg and yt-dlp use different executors."""
        from src.services.youtube import get_ffmpeg_executor, get_ytdl_executor
        
        ffmpeg_exec = get_ffmpeg_executor()
        ytdl_exec = get_ytdl_executor()
        
        assert ffmpeg_exec is not ytdl_exec


class TestOverwatchCogCaching:
    """Tests for Overwatch cog caching functions."""
    
    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset stats cache before each test."""
        from src.cogs import overwatch as ow_module
        ow_module._stats_cache.clear()
        yield
        ow_module._stats_cache.clear()
    
    def test_cache_stats_and_get(self):
        """Test caching and retrieving stats."""
        from src.cogs.overwatch import _cache_stats, _get_cached_stats
        
        stats = CompetitiveStats(
            tank=RankInfo(division="Diamond", tier=2),
            damage=RankInfo(division="Master", tier=3),
            support=RankInfo(division="Platinum", tier=1),
        )
        
        _cache_stats("Player#1234", stats)
        
        cached = _get_cached_stats("Player#1234")
        assert cached is not None
        assert cached.tank.division == "Diamond"
        assert cached.damage.division == "Master"
    
    def test_cache_miss(self):
        """Test cache miss returns None."""
        from src.cogs.overwatch import _get_cached_stats
        
        cached = _get_cached_stats("Nonexistent#1234")
        assert cached is None
    
    def test_cache_expired(self):
        """Test expired cache returns None."""
        from src.cogs.overwatch import _get_cached_stats, CACHE_TTL_SECONDS
        from src.cogs import overwatch as ow_module
        
        stats = CompetitiveStats(
            tank=RankInfo(division="Gold", tier=1),
        )
        
        # Manually add expired entry
        expired_time = time.time() - CACHE_TTL_SECONDS - 100
        ow_module._stats_cache["Expired#1234"] = (stats, expired_time)
        
        cached = _get_cached_stats("Expired#1234")
        assert cached is None
        
        # Entry should be removed
        assert "Expired#1234" not in ow_module._stats_cache
    
    def test_cache_not_expired(self):
        """Test valid cache entry is returned."""
        from src.cogs.overwatch import _get_cached_stats, _cache_stats
        
        stats = CompetitiveStats(
            tank=RankInfo(division="Silver", tier=5),
        )
        
        _cache_stats("Recent#1234", stats)
        
        # Should still be valid
        cached = _get_cached_stats("Recent#1234")
        assert cached is not None
        assert cached.tank.division == "Silver"
