"""Integration tests for API clients.

These tests make real API calls and should be run sparingly.
Mark them with @pytest.mark.integration and @pytest.mark.slow.
"""

import pytest

from src.services.overfast import OverfastClient
from src.services.youtube import YouTubeAudioClient


@pytest.mark.integration
@pytest.mark.slow
class TestOverfastClientIntegration:
    """Integration tests for Overfast API client."""
    
    @pytest.fixture
    def client(self) -> OverfastClient:
        """Create Overfast client."""
        return OverfastClient()
    
    @pytest.mark.asyncio
    async def test_check_health(self, client: OverfastClient):
        """Test API health check."""
        # This test requires network access, skip if it fails
        try:
            is_healthy = await client.check_health()
            assert is_healthy is True
        except Exception:
            pytest.skip("Network not available or API down")
    
    async def test_get_heroes(self, client: OverfastClient):
        """Test fetching hero list."""
        heroes = await client.get_heroes()
        
        assert isinstance(heroes, list)
        assert len(heroes) > 0
        # Check for known heroes
        hero_names = [h.get("name") for h in heroes]
        assert any("Tracer" in name for name in hero_names if name)
    
    async def test_get_maps(self, client: OverfastClient):
        """Test fetching map list."""
        maps = await client.get_maps()
        
        assert isinstance(maps, list)
        assert len(maps) > 0


@pytest.mark.integration
@pytest.mark.slow
class TestYouTubeClientIntegration:
    """Integration tests for YouTube audio client."""
    
    @pytest.fixture
    def client(self) -> YouTubeAudioClient:
        """Create YouTube client."""
        return YouTubeAudioClient()
    
    async def test_search(self, client: YouTubeAudioClient):
        """Test YouTube search."""
        results = await client.search("never gonna give you up", limit=3)
        
        assert isinstance(results, list)
        assert len(results) > 0
        assert "title" in results[0] or "id" in results[0]
    
    async def test_get_audio_track_by_search(self, client: YouTubeAudioClient):
        """Test getting audio track from search query."""
        track = await client.get_audio_track("rick astley never gonna give you up")
        
        assert track is not None
        assert track.title
        assert track.url
        assert track.duration > 0
    
    def test_is_youtube_url(self, client: YouTubeAudioClient):
        """Test YouTube URL detection."""
        assert client.is_youtube_url("https://www.youtube.com/watch?v=abc123")
        assert client.is_youtube_url("https://youtu.be/abc123")
        assert client.is_youtube_url("https://music.youtube.com/watch?v=abc123")
        assert not client.is_youtube_url("https://spotify.com/track/abc123")
    
    def test_is_playlist_url(self, client: YouTubeAudioClient):
        """Test playlist URL detection."""
        assert client.is_playlist_url("https://youtube.com/playlist?list=abc123")
        assert client.is_playlist_url("https://youtube.com/watch?v=abc&list=xyz")
        assert not client.is_playlist_url("https://youtube.com/watch?v=abc123")

