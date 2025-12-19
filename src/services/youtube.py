"""Audio client for music playback.

Uses yt-dlp to extract audio streams from YouTube and SoundCloud.
This is used for Discord voice channel playback.
"""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yt_dlp

from src.config.logging import get_logger

logger = get_logger(__name__)


def get_ffmpeg_executor() -> ThreadPoolExecutor:
    """Get the FFmpeg thread pool executor for non-blocking audio operations."""
    return _ffmpeg_executor


def get_ytdl_executor() -> ThreadPoolExecutor:
    """Get the yt-dlp thread pool executor for non-blocking extraction."""
    return _ytdl_executor

# Path to cookies file (for bypassing YouTube bot detection)
COOKIES_FILE = Path("/app/cookies.txt")
COOKIES_FILE_LOCAL = Path("cookies.txt")
COOKIES_TEMP_FILE = Path("/tmp/youtube_cookies.txt")

# Secret Manager path for cookies
YOUTUBE_COOKIES_SECRET = "projects/mr-swede/secrets/youtube-cookie/versions/latest"

# Dedicated thread pools to prevent blocking the main event loop
# yt-dlp: CPU-bound extraction, can be slow
_ytdl_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ytdl")
# FFmpeg: audio processing/conversion
_ffmpeg_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ffmpeg")

# Timeout for yt-dlp operations (seconds)
# Keep this SHORT to avoid blocking Discord heartbeats (must respond within 10s)
# If extraction takes longer, it's better to fail fast and retry
YTDL_TIMEOUT = 25  # Reduced to 25s to account for overhead

# Audio URL cache (URLs expire after ~6 hours, we cache for 4 hours)
_audio_url_cache: dict[str, tuple["AudioTrack", float]] = {}
AUDIO_URL_CACHE_TTL = 4 * 60 * 60  # 4 hours in seconds


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
        # Discord voice uses 48kHz 64kbps opus - no point getting high quality
        # Use permissive format selection to avoid "format not available" errors
        options = {
            # bestaudio = best available audio-only format
            # best = fallback to best video+audio if no audio-only available
            # This is more reliable than restrictive format filters
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
            # Socket timeout to prevent hanging - keep short for responsiveness
            "socket_timeout": 8,
            # HTTP chunk size for faster initial response
            "http_chunk_size": 1048576,  # 1MB chunks
            # Disable retries - APIs have strict rate limits
            "retries": 0,
            "fragment_retries": 0,
            "extractor_retries": 0,
            "file_access_retries": 0,
            # Skip unnecessary processing
            "skip_download": True,
            "no_check_formats": True,  # Skip format availability check (faster)
            "youtube_include_dash_manifest": False,  # Skip DASH (we don't need it)
            "youtube_include_hls_manifest": False,   # Skip HLS (we don't need it)
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
    
    def _get_cache_key(self, url_or_query: str) -> str:
        """Generate a cache key for a URL or query."""
        # Normalize YouTube URLs to video ID for better cache hits
        if "youtube.com" in url_or_query or "youtu.be" in url_or_query:
            import re
            # Extract video ID
            patterns = [
                r'(?:v=|/)([a-zA-Z0-9_-]{11})(?:[&?]|$)',
                r'youtu\.be/([a-zA-Z0-9_-]{11})',
            ]
            for pattern in patterns:
                match = re.search(pattern, url_or_query)
                if match:
                    return f"yt:{match.group(1)}"
        return url_or_query.lower().strip()
    
    def _get_cached_track(self, url_or_query: str) -> AudioTrack | None:
        """Get a track from cache if still valid."""
        cache_key = self._get_cache_key(url_or_query)
        if cache_key in _audio_url_cache:
            track, timestamp = _audio_url_cache[cache_key]
            age = time.time() - timestamp
            if age < AUDIO_URL_CACHE_TTL:
                remaining = AUDIO_URL_CACHE_TTL - age
                logger.info(
                    "📦 Audio cache HIT",
                    cache_key=cache_key,
                    title=track.title,
                    cache_age_minutes=round(age / 60, 1),
                    ttl_remaining_minutes=round(remaining / 60, 1),
                )
                return track
            else:
                logger.info(
                    "📦 Audio cache EXPIRED",
                    cache_key=cache_key,
                    cache_age_hours=round(age / 3600, 1),
                )
                del _audio_url_cache[cache_key]
        else:
            logger.info("📦 Audio cache MISS", cache_key=cache_key, query=url_or_query[:50])
        return None
    
    def _cache_track(self, url_or_query: str, track: AudioTrack) -> None:
        """Cache an audio track."""
        cache_key = self._get_cache_key(url_or_query)
        _audio_url_cache[cache_key] = (track, time.time())
        
        # Also cache by webpage_url if different
        if track.webpage_url and track.webpage_url != url_or_query:
            webpage_key = self._get_cache_key(track.webpage_url)
            _audio_url_cache[webpage_key] = (track, time.time())
        
        logger.info(
            "📦 Audio cache STORE",
            cache_key=cache_key,
            title=track.title,
            duration=track.duration_str,
            ttl_hours=AUDIO_URL_CACHE_TTL / 3600,
        )
    
    async def get_audio_track(
        self, 
        url_or_query: str, 
        use_cache: bool = True,
    ) -> AudioTrack | None:
        """Extract audio track from a URL or search query.
        
        Args:
            url_or_query: YouTube URL or search query
            use_cache: Whether to use cached results (default: True)
            
        Returns:
            AudioTrack if successful, None otherwise
        """
        # Check cache first
        if use_cache:
            cached = self._get_cached_track(url_or_query)
            if cached:
                logger.info(
                    "✅ get_audio_track CACHE HIT (instant)",
                    title=cached.title,
                    duration=cached.duration_str,
                )
                return cached
        
        options = self._ensure_initialized()
        
        is_url = url_or_query.startswith(("http://", "https://"))
        
        def _extract() -> dict[str, Any] | None:
            extract_start = time.time()
            try:
                logger.info(
                    "🔧 Creating yt-dlp instance",
                    has_cookies=options.get("cookiefile") is not None,
                    format=options.get("format", "default")[:50],
                )
                
                with yt_dlp.YoutubeDL(options) as ydl:
                    ydl_init_time = time.time() - extract_start
                    logger.info("🔧 yt-dlp initialized", init_ms=round(ydl_init_time * 1000))
                    
                    # If it's not a URL, search YouTube
                    if not is_url:
                        search_url = f"ytsearch1:{url_or_query}"
                        logger.info(
                            "🔍 YouTube SEARCH starting...",
                            query=url_or_query,
                        )
                        
                        search_start = time.time()
                        info = ydl.extract_info(search_url, download=False)
                        search_elapsed = time.time() - search_start
                        
                        if not info:
                            logger.warning(
                                "🔍 YouTube SEARCH returned None",
                                query=url_or_query,
                                search_ms=round(search_elapsed * 1000),
                            )
                            return None
                        
                        # Handle search results
                        if "entries" in info:
                            entries = info.get("entries", [])
                            entry_count = len(entries) if entries else 0
                            logger.info(
                                "🔍 YouTube SEARCH got results",
                                entry_count=entry_count,
                                search_ms=round(search_elapsed * 1000),
                            )
                            
                            if not entries:
                                logger.warning(
                                    "🔍 YouTube SEARCH empty entries",
                                    query=url_or_query,
                                )
                                return None
                            
                            info = entries[0]
                            if not info:
                                logger.warning(
                                    "🔍 YouTube SEARCH first entry is None",
                                    query=url_or_query,
                                )
                                return None
                        
                        total_elapsed = time.time() - extract_start
                        logger.info(
                            "✅ YouTube SEARCH complete",
                            query=url_or_query,
                            title=info.get("title"),
                            video_id=info.get("id"),
                            duration=info.get("duration"),
                            search_ms=round(search_elapsed * 1000),
                            total_ms=round(total_elapsed * 1000),
                            has_url="url" in info,
                            format_count=len(info.get("formats", [])),
                        )
                    else:
                        logger.info(
                            "🎬 YouTube EXTRACT starting...",
                            url=url_or_query[:80],
                        )
                        
                        extract_api_start = time.time()
                        info = ydl.extract_info(url_or_query, download=False)
                        extract_elapsed = time.time() - extract_api_start
                        
                        total_elapsed = time.time() - extract_start
                        logger.info(
                            "✅ YouTube EXTRACT complete",
                            title=info.get("title") if info else None,
                            video_id=info.get("id") if info else None,
                            extract_ms=round(extract_elapsed * 1000),
                            total_ms=round(total_elapsed * 1000),
                            has_url="url" in info if info else False,
                            format_count=len(info.get("formats", [])) if info else 0,
                        )
                    
                    return info
            except Exception as e:
                elapsed = time.time() - extract_start
                error_str = str(e)
                # Detect common YouTube errors
                if "Sign in" in error_str or "bot" in error_str.lower():
                    logger.error(
                        "❌ YouTube AUTH ERROR (cookies may be needed/expired)",
                        url=url_or_query[:80],
                        error=error_str[:200],
                        elapsed_ms=round(elapsed * 1000),
                    )
                elif "Video unavailable" in error_str:
                    logger.warning(
                        "❌ Video unavailable",
                        url=url_or_query[:80],
                        elapsed_ms=round(elapsed * 1000),
                    )
                elif "No video formats" in error_str or "Unable to extract" in error_str:
                    logger.error(
                        "❌ YouTube format extraction FAILED",
                        url=url_or_query[:80],
                        error=error_str[:200],
                        elapsed_ms=round(elapsed * 1000),
                        hint="Video may be age-restricted or geo-blocked",
                    )
                else:
                    logger.error(
                        "❌ YouTube extraction FAILED",
                        url=url_or_query[:80],
                        error=error_str[:200],
                        elapsed_ms=round(elapsed * 1000),
                    )
                return None
        
        logger.info(
            "🎵 get_audio_track START",
            query=url_or_query[:80],
            is_url=is_url,
            use_cache=use_cache,
        )
        
        loop = asyncio.get_running_loop()
        overall_start = time.time()
        try:
            info = await asyncio.wait_for(
                loop.run_in_executor(_ytdl_executor, _extract),
                timeout=YTDL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            elapsed = time.time() - overall_start
            logger.error(
                "⏱️ Audio extraction TIMED OUT",
                url=url_or_query[:80],
                timeout_seconds=YTDL_TIMEOUT,
                elapsed_ms=round(elapsed * 1000),
            )
            # Raise TimeoutError so caller knows it was a timeout, not "no results"
            raise asyncio.TimeoutError(f"YouTube extraction timed out after {YTDL_TIMEOUT}s")
        
        if not info:
            elapsed = time.time() - overall_start
            logger.warning(
                "🎵 get_audio_track returned None (no info from yt-dlp)",
                query=url_or_query[:80],
                elapsed_ms=round(elapsed * 1000),
            )
            return None
        
        # Find audio format URL - prefer lowest quality (Discord only supports 64kbps anyway)
        format_start = time.time()
        audio_url = info.get("url")
        selected_format = "direct_url"
        
        if not audio_url:
            # Try to get from formats
            formats = info.get("formats", [])
            audio_formats = [f for f in formats if f.get("acodec") != "none"]
            
            logger.info(
                "🔊 Selecting audio format",
                total_formats=len(formats),
                audio_formats=len(audio_formats),
                title=info.get("title"),
            )
            
            if audio_formats:
                # Sort by bitrate (lowest first) - we don't need high quality
                audio_formats.sort(key=lambda f: f.get("abr") or f.get("tbr") or 999)
                
                # Log available formats for debugging
                format_summary = [
                    {
                        "id": f.get("format_id"),
                        "codec": f.get("acodec"),
                        "abr": f.get("abr"),
                        "ext": f.get("ext"),
                    }
                    for f in audio_formats[:5]  # Log top 5
                ]
                logger.debug("Available audio formats (top 5)", formats=format_summary)
                
                # Prefer opus (Discord native) at lowest bitrate
                for fmt in audio_formats:
                    if fmt.get("acodec") == "opus":
                        audio_url = fmt.get("url")
                        selected_format = f"opus_{fmt.get('abr', '?')}kbps"
                        break
                
                # Fall back to any low-bitrate format
                if not audio_url:
                    chosen = audio_formats[0]
                    audio_url = chosen.get("url")
                    selected_format = f"{chosen.get('acodec', '?')}_{chosen.get('abr', '?')}kbps"
        
        format_elapsed = time.time() - format_start
        
        if not audio_url:
            logger.error(
                "❌ No audio URL found in response",
                title=info.get("title"),
                video_id=info.get("id"),
                format_count=len(info.get("formats", [])),
            )
            return None
        
        logger.info(
            "🔊 Audio format selected",
            format=selected_format,
            format_selection_ms=round(format_elapsed * 1000),
            url_length=len(audio_url),
        )
        
        track = AudioTrack(
            url=audio_url,
            title=info.get("title", "Unknown"),
            duration=info.get("duration", 0),
            thumbnail=info.get("thumbnail"),
            webpage_url=info.get("webpage_url", url_or_query),
            uploader=info.get("uploader", "Unknown"),
        )
        
        # Cache the track for future requests
        self._cache_track(url_or_query, track)
        
        total_elapsed = time.time() - overall_start
        logger.info(
            "✅ get_audio_track COMPLETE",
            title=track.title,
            duration=track.duration_str,
            format=selected_format,
            total_ms=round(total_elapsed * 1000),
            cached=False,
        )
        
        return track
    
    async def prefetch_track(self, url_or_query: str) -> None:
        """Pre-fetch a track in the background (for queue pre-loading).
        
        This method doesn't return anything - it just ensures the track
        is cached for when it's actually needed.
        
        Args:
            url_or_query: YouTube URL or search query
        """
        # Skip if already cached
        if self._get_cached_track(url_or_query):
            return
        
        # Fetch in background (fire and forget)
        try:
            await self.get_audio_track(url_or_query)
            logger.debug("Pre-fetched track", query=url_or_query)
        except Exception as e:
            logger.debug("Pre-fetch failed (non-critical)", query=url_or_query, error=str(e))
    
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

