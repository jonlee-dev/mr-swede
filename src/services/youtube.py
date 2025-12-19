"""YouTube audio client for music playback.

Uses yt-dlp to extract audio streams from YouTube videos.
This is used for Discord voice channel playback.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import yt_dlp

from src.config.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AudioTrack:
    """Represents an audio track ready for playback."""
    
    url: str  # Direct audio stream URL
    title: str
    duration: int  # Duration in seconds
    thumbnail: str | None = None
    webpage_url: str = ""  # Original YouTube URL
    uploader: str = ""
    
    @property
    def duration_str(self) -> str:
        """Get duration as formatted string (MM:SS or HH:MM:SS)."""
        hours, remainder = divmod(self.duration, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"


class YouTubeAudioClient:
    """Client for extracting audio from YouTube videos."""
    
    # yt-dlp options for audio extraction
    YDL_OPTIONS = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "nocheckcertificate": True,
        "ignoreerrors": False,
        "logtostderr": False,
        "geo_bypass": True,
        "source_address": "0.0.0.0",
    }
    
    # FFmpeg options for Discord playback
    FFMPEG_OPTIONS = {
        "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        "options": "-vn",  # No video
    }
    
    def __init__(self) -> None:
        """Initialize the YouTube audio client."""
        self._ydl = yt_dlp.YoutubeDL(self.YDL_OPTIONS)
    
    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search YouTube for videos.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of video info dictionaries
        """
        search_opts = {
            **self.YDL_OPTIONS,
            "extract_flat": True,
            "playlistend": limit,
        }
        
        def _search() -> list[dict[str, Any]]:
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                results = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                if results and "entries" in results:
                    return list(results["entries"])
                return []
        
        logger.info("Searching YouTube", query=query, limit=limit)
        return await asyncio.get_event_loop().run_in_executor(None, _search)
    
    async def get_audio_track(self, url_or_query: str) -> AudioTrack | None:
        """Extract audio track from a URL or search query.
        
        Args:
            url_or_query: YouTube URL or search query
            
        Returns:
            AudioTrack if successful, None otherwise
        """
        def _extract() -> dict[str, Any] | None:
            try:
                # If it's not a URL, search for it
                if not url_or_query.startswith(("http://", "https://")):
                    search_url = f"ytsearch1:{url_or_query}"
                    info = self._ydl.extract_info(search_url, download=False)
                    if info and "entries" in info and info["entries"]:
                        info = info["entries"][0]
                else:
                    info = self._ydl.extract_info(url_or_query, download=False)
                
                return info
            except Exception as e:
                logger.error("Failed to extract audio", url=url_or_query, error=str(e))
                return None
        
        logger.info("Extracting audio", url=url_or_query)
        info = await asyncio.get_event_loop().run_in_executor(None, _extract)
        
        if not info:
            return None
        
        # Find the best audio format URL
        audio_url = info.get("url")
        if not audio_url:
            # Try to get from formats
            formats = info.get("formats", [])
            audio_formats = [f for f in formats if f.get("acodec") != "none"]
            if audio_formats:
                # Prefer opus or webm
                for fmt in audio_formats:
                    if fmt.get("acodec") == "opus":
                        audio_url = fmt.get("url")
                        break
                if not audio_url:
                    audio_url = audio_formats[-1].get("url")
        
        if not audio_url:
            logger.error("No audio URL found", title=info.get("title"))
            return None
        
        return AudioTrack(
            url=audio_url,
            title=info.get("title", "Unknown"),
            duration=info.get("duration", 0),
            thumbnail=info.get("thumbnail"),
            webpage_url=info.get("webpage_url", url_or_query),
            uploader=info.get("uploader", "Unknown"),
        )
    
    async def get_playlist_tracks(
        self, 
        playlist_url: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get tracks from a YouTube playlist.
        
        Args:
            playlist_url: YouTube playlist URL
            limit: Maximum tracks to extract
            
        Returns:
            List of track info dictionaries
        """
        playlist_opts = {
            **self.YDL_OPTIONS,
            "extract_flat": True,
            "playlistend": limit,
            "noplaylist": False,
        }
        
        def _extract() -> list[dict[str, Any]]:
            try:
                with yt_dlp.YoutubeDL(playlist_opts) as ydl:
                    info = ydl.extract_info(playlist_url, download=False)
                    if info and "entries" in info:
                        return [
                            entry for entry in info["entries"]
                            if entry is not None
                        ]
                return []
            except Exception as e:
                logger.error("Failed to extract playlist", url=playlist_url, error=str(e))
                return []
        
        logger.info("Extracting playlist", url=playlist_url, limit=limit)
        return await asyncio.get_event_loop().run_in_executor(None, _extract)
    
    def is_youtube_url(self, url: str) -> bool:
        """Check if a URL is a YouTube URL.
        
        Args:
            url: URL to check
            
        Returns:
            True if YouTube URL
        """
        youtube_patterns = [
            "youtube.com/watch",
            "youtu.be/",
            "youtube.com/playlist",
            "music.youtube.com/",
        ]
        return any(pattern in url for pattern in youtube_patterns)
    
    def is_playlist_url(self, url: str) -> bool:
        """Check if a URL is a YouTube playlist URL.
        
        Args:
            url: URL to check
            
        Returns:
            True if playlist URL
        """
        return "playlist" in url or "list=" in url

