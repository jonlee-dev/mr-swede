"""Spotify API client for music features.

Note: Spotify's API can provide track metadata, playlists, and search,
but actual audio streaming requires Spotify Premium and is restricted.
For Discord voice playback, we'll use Spotify for search/metadata and
YouTube for the actual audio stream.
"""

from typing import Any

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

from src.config.logging import get_logger
from src.config.secrets import get_secrets
from src.config.settings import get_settings

logger = get_logger(__name__)


class SpotifyClient:
    """Client for Spotify API interactions."""
    
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
    ) -> None:
        """Initialize Spotify client.
        
        Args:
            client_id: Spotify application client ID
            client_secret: Spotify application client secret
            redirect_uri: OAuth redirect URI
        """
        settings = get_settings()
        secrets = get_secrets()
        
        # Get credentials from secrets or parameters
        if client_id and client_secret:
            self._client_id = client_id
            self._client_secret = client_secret
        elif secrets.spotify:
            self._client_id = secrets.spotify.client_id
            self._client_secret = secrets.spotify.client_secret
        elif settings.spotify_client_id and settings.spotify_client_secret:
            self._client_id = settings.spotify_client_id
            self._client_secret = settings.spotify_client_secret.get_secret_value()
        else:
            raise ValueError(
                "Spotify credentials not found. "
                "Configure in GSM or set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET env vars."
            )
        
        self._redirect_uri = redirect_uri or settings.spotify_redirect_uri
        
        # Client credentials flow (no user auth needed)
        self._client: spotipy.Spotify | None = None
        
        # User-authorized client (for user-specific features)
        self._user_clients: dict[str, spotipy.Spotify] = {}
    
    @property
    def client(self) -> spotipy.Spotify:
        """Get the client credentials Spotify client."""
        if self._client is None:
            auth_manager = SpotifyClientCredentials(
                client_id=self._client_id,
                client_secret=self._client_secret,
            )
            self._client = spotipy.Spotify(auth_manager=auth_manager)
        return self._client
    
    def get_auth_url(self, state: str | None = None) -> str:
        """Get Spotify OAuth authorization URL.
        
        Args:
            state: State parameter for OAuth
            
        Returns:
            Authorization URL
        """
        auth_manager = SpotifyOAuth(
            client_id=self._client_id,
            client_secret=self._client_secret,
            redirect_uri=self._redirect_uri,
            scope="user-read-playback-state user-read-currently-playing playlist-read-private",
            state=state,
        )
        return auth_manager.get_authorize_url()
    
    # ==================== Search ====================
    
    async def search_tracks(
        self, 
        query: str, 
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search for tracks.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of track data
        """
        logger.info("Searching Spotify tracks", query=query)
        results = self.client.search(q=query, type="track", limit=limit)
        
        tracks = []
        for item in results.get("tracks", {}).get("items", []):
            tracks.append({
                "id": item["id"],
                "name": item["name"],
                "artists": [a["name"] for a in item["artists"]],
                "album": item["album"]["name"],
                "duration_ms": item["duration_ms"],
                "url": item["external_urls"].get("spotify"),
                "preview_url": item.get("preview_url"),
                "image_url": item["album"]["images"][0]["url"] if item["album"]["images"] else None,
            })
        
        return tracks
    
    async def search_artists(
        self, 
        query: str, 
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search for artists.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of artist data
        """
        results = self.client.search(q=query, type="artist", limit=limit)
        
        artists = []
        for item in results.get("artists", {}).get("items", []):
            artists.append({
                "id": item["id"],
                "name": item["name"],
                "genres": item.get("genres", []),
                "followers": item["followers"]["total"],
                "url": item["external_urls"].get("spotify"),
                "image_url": item["images"][0]["url"] if item["images"] else None,
            })
        
        return artists
    
    async def search_playlists(
        self, 
        query: str, 
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search for playlists.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of playlist data
        """
        results = self.client.search(q=query, type="playlist", limit=limit)
        
        playlists = []
        for item in results.get("playlists", {}).get("items", []):
            playlists.append({
                "id": item["id"],
                "name": item["name"],
                "owner": item["owner"]["display_name"],
                "tracks_total": item["tracks"]["total"],
                "url": item["external_urls"].get("spotify"),
                "image_url": item["images"][0]["url"] if item["images"] else None,
            })
        
        return playlists
    
    # ==================== Track/Album/Playlist Data ====================
    
    async def get_track(self, track_id: str) -> dict[str, Any] | None:
        """Get track details.
        
        Args:
            track_id: Spotify track ID
            
        Returns:
            Track data or None
        """
        try:
            track = self.client.track(track_id)
            return {
                "id": track["id"],
                "name": track["name"],
                "artists": [a["name"] for a in track["artists"]],
                "album": track["album"]["name"],
                "duration_ms": track["duration_ms"],
                "url": track["external_urls"].get("spotify"),
                "isrc": track.get("external_ids", {}).get("isrc"),
            }
        except Exception as e:
            logger.error("Failed to get track", track_id=track_id, error=str(e))
            return None
    
    async def get_playlist_tracks(
        self, 
        playlist_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get tracks from a playlist.
        
        Args:
            playlist_id: Spotify playlist ID
            limit: Maximum tracks to return
            
        Returns:
            List of track data
        """
        tracks = []
        results = self.client.playlist_tracks(playlist_id, limit=min(limit, 100))
        
        while results and len(tracks) < limit:
            for item in results.get("items", []):
                track = item.get("track")
                if track:
                    tracks.append({
                        "id": track["id"],
                        "name": track["name"],
                        "artists": [a["name"] for a in track["artists"]],
                        "album": track["album"]["name"],
                        "duration_ms": track["duration_ms"],
                        "url": track["external_urls"].get("spotify"),
                    })
            
            if results.get("next") and len(tracks) < limit:
                results = self.client.next(results)
            else:
                break
        
        return tracks[:limit]
    
    async def get_album_tracks(self, album_id: str) -> list[dict[str, Any]]:
        """Get tracks from an album.
        
        Args:
            album_id: Spotify album ID
            
        Returns:
            List of track data
        """
        album = self.client.album(album_id)
        tracks = []
        
        for track in album.get("tracks", {}).get("items", []):
            tracks.append({
                "id": track["id"],
                "name": track["name"],
                "artists": [a["name"] for a in track["artists"]],
                "album": album["name"],
                "duration_ms": track["duration_ms"],
                "track_number": track["track_number"],
            })
        
        return tracks
    
    # ==================== Utilities ====================
    
    def parse_spotify_url(self, url: str) -> tuple[str, str] | None:
        """Parse a Spotify URL to extract type and ID.
        
        Args:
            url: Spotify URL or URI
            
        Returns:
            Tuple of (type, id) or None if invalid
        """
        import re
        
        # Handle Spotify URIs (spotify:track:xxx)
        uri_match = re.match(r"spotify:(track|album|playlist|artist):([a-zA-Z0-9]+)", url)
        if uri_match:
            return uri_match.group(1), uri_match.group(2)
        
        # Handle Spotify URLs
        url_match = re.match(
            r"https?://open\.spotify\.com/(track|album|playlist|artist)/([a-zA-Z0-9]+)",
            url,
        )
        if url_match:
            return url_match.group(1), url_match.group(2)
        
        return None
    
    async def get_search_query_for_youtube(self, track_id: str) -> str | None:
        """Generate a YouTube search query from a Spotify track.
        
        Args:
            track_id: Spotify track ID
            
        Returns:
            Search query for YouTube or None
        """
        track = await self.get_track(track_id)
        if not track:
            return None
        
        artists = " ".join(track["artists"][:2])
        return f"{artists} {track['name']} audio"


def get_spotify_client() -> SpotifyClient | None:
    """Get a Spotify client if credentials are available.
    
    Returns:
        SpotifyClient or None if credentials not configured
    """
    try:
        return SpotifyClient()
    except ValueError:
        logger.warning("Spotify client not available - credentials not configured")
        return None
