"""YouTube audio client for music playback.

Uses yt-dlp to extract audio streams from YouTube videos.
This is used for Discord voice channel playback.
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import yt_dlp

from src.config.logging import get_logger

logger = get_logger(__name__)

# Path to cookies file (for bypassing YouTube bot detection)
COOKIES_FILE = Path("/app/cookies.txt")
COOKIES_FILE_LOCAL = Path("cookies.txt")
COOKIES_TEMP_FILE = Path("/tmp/youtube_cookies.txt")

# Secret Manager path for cookies
YOUTUBE_COOKIES_SECRET = "projects/mr-swede/secrets/youtube-cookie/versions/latest"

# Dedicated thread pool for yt-dlp operations (prevents blocking main event loop)
_ytdl_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ytdl")

# Timeout for yt-dlp operations (seconds)
YTDL_TIMEOUT = 30


def _fetch_cookies_from_gsm_sync() -> str | None:
    """Fetch YouTube cookies from Google Secret Manager (synchronous).
    
    Returns:
        Path to temporary cookies file, or None if not available
    """
    # Check if we already have the temp file
    if COOKIES_TEMP_FILE.exists() and COOKIES_TEMP_FILE.stat().st_size > 0:
        logger.debug("Using cached cookies from temp file")
        return str(COOKIES_TEMP_FILE)
    
    # Check for custom secret path via env var
    secret_path = os.environ.get("YOUTUBE_COOKIES_SECRET_PATH", YOUTUBE_COOKIES_SECRET)
    
    try:
        from google.cloud import secretmanager
        
        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(request={"name": secret_path})
        cookies_content = response.payload.data.decode("UTF-8")
        
        # Write to temp file
        COOKIES_TEMP_FILE.write_text(cookies_content)
        logger.info("Loaded YouTube cookies from Secret Manager")
        
        return str(COOKIES_TEMP_FILE)
        
    except ImportError:
        logger.debug("google-cloud-secret-manager not installed")
        return None
    except Exception as e:
        # This is expected if the secret doesn't exist
        logger.debug("Could not fetch YouTube cookies from GSM", error=str(e))
        return None


async def _fetch_cookies_from_gsm_async() -> str | None:
    """Fetch YouTube cookies from GSM asynchronously."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_ytdl_executor, _fetch_cookies_from_gsm_sync)


def _get_cookies_path_sync() -> str | None:
    """Get the path to the cookies file if available (synchronous).
    
    Checks in order:
    1. Cached temp file (already fetched from GSM)
    2. Local file at /app/cookies.txt (Docker)
    3. Local file at ./cookies.txt (development)
    
    NOTE: Does NOT fetch from GSM - use preload_cookies() first!
    """
    # Check cached temp file first (from prior GSM fetch)
    if COOKIES_TEMP_FILE.exists() and COOKIES_TEMP_FILE.stat().st_size > 0:
        return str(COOKIES_TEMP_FILE)
    
    # Fall back to local files
    for path in [COOKIES_FILE, COOKIES_FILE_LOCAL]:
        if path.exists() and path.stat().st_size > 0:
            logger.info("Using YouTube cookies from file", path=str(path))
            return str(path)
    
    return None


async def preload_cookies() -> None:
    """Pre-load cookies from GSM during bot startup.
    
    Call this during bot initialization to avoid blocking during playback.
    """
    logger.info("Pre-loading YouTube cookies...")
    await _fetch_cookies_from_gsm_async()
    
    if COOKIES_TEMP_FILE.exists():
        logger.info("YouTube cookies loaded successfully")
    else:
        logger.warning(
            "No YouTube cookies available - playback may fail. "
            "See TODO.md for instructions on setting up cookies."
        )


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
    
    # FFmpeg options for Discord playback
    FFMPEG_OPTIONS = {
        "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        "options": "-vn",  # No video
    }
    
    def __init__(self) -> None:
        """Initialize the YouTube audio client.
        
        NOTE: Call preload_cookies() before using this client to ensure
        cookies are loaded without blocking the event loop.
        """
        self._options: dict[str, Any] | None = None
        self._initialized = False
    
    def _ensure_initialized(self) -> dict[str, Any]:
        """Lazily initialize options (uses cached cookies only)."""
        if self._options is None:
            self._options = self._build_options()
            self._initialized = True
        return self._options
    
    def _build_options(self) -> dict[str, Any]:
        """Build yt-dlp options, including cookies if available."""
        options = {
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
            # Socket timeout to prevent hanging
            "socket_timeout": 15,
            # Disable retries - APIs have strict rate limits
            "retries": 0,
            "fragment_retries": 0,
            "extractor_retries": 0,
            "file_access_retries": 0,
        }
        
        # Add cookies if available (uses cached cookies only - no GSM call here)
        cookies_path = _get_cookies_path_sync()
        if cookies_path:
            options["cookiefile"] = cookies_path
            logger.info("YouTube cookies configured", path=cookies_path)
        else:
            logger.warning(
                "No YouTube cookies found - may be blocked by YouTube bot detection. "
                "See TODO.md for instructions on adding cookies."
            )
        
        return options
    
    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search YouTube for videos.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of video info dictionaries
        """
        options = self._ensure_initialized()
        search_opts = {
            **options,
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
        
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_ytdl_executor, _search),
                timeout=YTDL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error("YouTube search timed out", query=query)
            return []
    
    async def get_audio_track(self, url_or_query: str) -> AudioTrack | None:
        """Extract audio track from a URL or search query.
        
        Args:
            url_or_query: YouTube URL or search query
            
        Returns:
            AudioTrack if successful, None otherwise
        """
        options = self._ensure_initialized()
        
        def _extract() -> dict[str, Any] | None:
            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    # If it's not a URL, search for it
                    if not url_or_query.startswith(("http://", "https://")):
                        search_url = f"ytsearch1:{url_or_query}"
                        info = ydl.extract_info(search_url, download=False)
                        if info and "entries" in info and info["entries"]:
                            info = info["entries"][0]
                    else:
                        info = ydl.extract_info(url_or_query, download=False)
                    
                    return info
            except Exception as e:
                logger.error("Failed to extract audio", url=url_or_query, error=str(e))
                return None
        
        logger.info("Extracting audio", url=url_or_query)
        
        loop = asyncio.get_running_loop()
        try:
            info = await asyncio.wait_for(
                loop.run_in_executor(_ytdl_executor, _extract),
                timeout=YTDL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error("Audio extraction timed out", url=url_or_query)
            return None
        
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
        options = self._ensure_initialized()
        playlist_opts = {
            **options,
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
        
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_ytdl_executor, _extract),
                timeout=YTDL_TIMEOUT * 2,  # Longer timeout for playlists
            )
        except asyncio.TimeoutError:
            logger.error("Playlist extraction timed out", url=playlist_url)
            return []
    
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

