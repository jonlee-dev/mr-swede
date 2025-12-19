"""Overfast API client for Overwatch stats.

Overfast API is a community-maintained API that scrapes Overwatch profile data.
API Documentation: https://overfast-api.tekrop.fr/

NOTE: This is a FREE community API with STRICT rate limits (~1 req/sec).
All requests go through a global rate limiter to avoid 429 errors.
"""

import asyncio
import time
from typing import Any

from src.config.logging import get_logger
from src.database.models import CompetitiveStats, RankInfo
from src.services.base import BaseAPIClient

logger = get_logger(__name__)

# Overfast API base URL
OVERFAST_API_URL = "https://overfast-api.tekrop.fr"

# Global rate limiter - Overfast API allows ~1 request per second
# Using 2s to be safe (they have strict rate limits)
_last_request_time: float = 0
_rate_limit_lock = asyncio.Lock()
RATE_LIMIT_INTERVAL = 2.0  # seconds between requests


async def _wait_for_rate_limit() -> None:
    """Wait if needed to respect API rate limits.
    
    Uses asyncio.sleep which is non-blocking to the event loop.
    """
    global _last_request_time
    
    # Use timeout on lock acquisition to prevent deadlocks
    try:
        async with asyncio.timeout(5.0):
            async with _rate_limit_lock:
                now = time.time()
                elapsed = now - _last_request_time
                
                if elapsed < RATE_LIMIT_INTERVAL:
                    wait_time = RATE_LIMIT_INTERVAL - elapsed
                    logger.debug(
                        "⏳ Rate limit wait",
                        wait_seconds=round(wait_time, 2),
                        last_request_ago=round(elapsed, 2),
                    )
                    await asyncio.sleep(wait_time)
                
                _last_request_time = time.time()
    except asyncio.TimeoutError:
        logger.warning("Rate limit lock acquisition timed out, proceeding anyway")
        _last_request_time = time.time()


class OverfastClient(BaseAPIClient):
    """Client for the Overfast API.
    
    All requests are automatically rate-limited to ~1 req/sec to avoid 429 errors.
    """
    
    def __init__(self) -> None:
        """Initialize the Overfast API client."""
        # Custom User-Agent to differentiate from other GCP users
        # This may help avoid shared rate limiting on Cloud Run
        super().__init__(
            base_url=OVERFAST_API_URL, 
            timeout=15.0,
            headers={
                "User-Agent": "MrSwedeBot/2.1 (Discord Bot; +https://github.com/jonlee-dev/mr-swede)",
            },
        )
    
    @staticmethod
    def normalize_battle_tag(battle_tag: str) -> str:
        """Convert BattleTag to API-compatible format.
        
        Args:
            battle_tag: BattleTag in format "Name#1234" or "Name-1234"
            
        Returns:
            BattleTag in format "Name-1234"
        """
        return battle_tag.replace("#", "-")
    
    async def get_player_summary(self, battle_tag: str) -> dict[str, Any]:
        """Get player summary including competitive ranks.
        
        Args:
            battle_tag: Player's BattleTag
            
        Returns:
            Player summary data
            
        Raises:
            httpx.HTTPStatusError: If player not found or API error
        """
        normalized_tag = self.normalize_battle_tag(battle_tag)
        endpoint = f"/players/{normalized_tag}/summary"
        url = f"{self.base_url}{endpoint}"
        
        # Wait for rate limit before making request
        await _wait_for_rate_limit()
        
        # Log time since last request to help debug rate limiting
        time_since_last = time.time() - _last_request_time if _last_request_time > 0 else None
        
        logger.info(
            "🎮 Overfast API request START",
            method="GET",
            endpoint=endpoint,
            url=url,
            battle_tag=battle_tag,
            seconds_since_last_request=round(time_since_last, 1) if time_since_last else "first_request",
        )
        
        start_time = time.time()
        try:
            result = await self._get(endpoint)
            elapsed = time.time() - start_time
            logger.info(
                "✅ Overfast API request SUCCESS",
                endpoint=endpoint,
                battle_tag=battle_tag,
                elapsed_ms=round(elapsed * 1000),
                response_keys=list(result.keys()) if isinstance(result, dict) else None,
            )
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            error_str = str(e)
            
            # Check if it's a 429 rate limit error
            if "429" in error_str:
                logger.error(
                    "🚫 Overfast API RATE LIMITED (429)",
                    endpoint=endpoint,
                    battle_tag=battle_tag,
                    elapsed_ms=round(elapsed * 1000),
                    seconds_since_last_request=round(time_since_last, 1) if time_since_last else "first_request",
                    hint="This may be due to shared IP on Cloud Run or API-wide rate limits",
                )
            else:
                logger.error(
                    "❌ Overfast API request FAILED",
                    endpoint=endpoint,
                    battle_tag=battle_tag,
                    elapsed_ms=round(elapsed * 1000),
                    error=error_str[:200],
                )
            raise
    
    async def get_player_stats(
        self, 
        battle_tag: str, 
        gamemode: str = "competitive",
        platform: str = "pc",
    ) -> dict[str, Any]:
        """Get detailed player statistics.
        
        Args:
            battle_tag: Player's BattleTag
            gamemode: Game mode ("competitive" or "quickplay")
            platform: Platform ("pc" or "console")
            
        Returns:
            Player statistics data
        """
        normalized_tag = self.normalize_battle_tag(battle_tag)
        endpoint = f"/players/{normalized_tag}/stats"
        
        params = {
            "gamemode": gamemode,
            "platform": platform,
        }
        
        await _wait_for_rate_limit()
        logger.info("Fetching player stats", battle_tag=battle_tag, gamemode=gamemode)
        return await self._get(endpoint, params=params)
    
    async def get_player_career(self, battle_tag: str) -> dict[str, Any]:
        """Get player career profile data.
        
        Args:
            battle_tag: Player's BattleTag
            
        Returns:
            Player career data
        """
        normalized_tag = self.normalize_battle_tag(battle_tag)
        endpoint = f"/players/{normalized_tag}"
        
        await _wait_for_rate_limit()
        logger.info("Fetching player career", battle_tag=battle_tag)
        return await self._get(endpoint)
    
    async def get_competitive_stats(self, battle_tag: str) -> CompetitiveStats:
        """Get competitive stats in structured format.
        
        Args:
            battle_tag: Player's BattleTag
            
        Returns:
            CompetitiveStats model with rank information
        """
        try:
            summary = await self.get_player_summary(battle_tag)
            
            logger.debug("Player summary response", battle_tag=battle_tag, summary_keys=list(summary.keys()))
            
            competitive = summary.get("competitive", {})
            if not competitive:
                logger.warning("No competitive data found", battle_tag=battle_tag)
                return CompetitiveStats()
            
            logger.debug("Competitive data", battle_tag=battle_tag, competitive=competitive)
            
            # Handle both 'pc' and 'console' keys
            pc_data = competitive.get("pc", {})
            if not pc_data:
                # Try to get from season data directly (API structure varies)
                pc_data = competitive.get("season", {})
            
            logger.debug("PC data", battle_tag=battle_tag, pc_data=pc_data)
            
            # Parse rank data for each role
            def parse_rank(role_data: Any) -> RankInfo:
                if not role_data:
                    return RankInfo()
                # Handle case where role_data might not be a dict
                if not isinstance(role_data, dict):
                    logger.warning("Unexpected role data type", 
                                   role_data_type=type(role_data).__name__, 
                                   role_data=role_data)
                    return RankInfo()
                return RankInfo(
                    division=role_data.get("division", ""),
                    tier=role_data.get("tier", 0) if isinstance(role_data.get("tier"), int) else 0,
                    skill_rating=role_data.get("skill_rating"),
                )
            
            # Get season info if available
            season = None
            if "season" in pc_data and isinstance(pc_data.get("season"), dict):
                season_data = pc_data.get("season", {})
                tank_data = season_data.get("tank")
                damage_data = season_data.get("damage")
                support_data = season_data.get("support")
            else:
                tank_data = pc_data.get("tank")
                damage_data = pc_data.get("damage")
                support_data = pc_data.get("support")
            
            logger.debug("Role data", tank=tank_data, damage=damage_data, support=support_data)
            
            stats = CompetitiveStats(
                tank=parse_rank(tank_data),
                damage=parse_rank(damage_data),
                support=parse_rank(support_data),
                season=season,
            )
            
            logger.info(
                "Fetched competitive stats",
                battle_tag=battle_tag,
                tank=stats.tank.display,
                damage=stats.damage.display,
                support=stats.support.display,
            )
            
            return stats
            
        except Exception as e:
            logger.error("Failed to get competitive stats", battle_tag=battle_tag, error=str(e))
            raise
    
    async def search_players(self, name: str, limit: int = 25) -> list[dict[str, Any]]:
        """Search for players by name.
        
        Args:
            name: Player name to search for
            limit: Maximum results to return
            
        Returns:
            List of matching players
        """
        endpoint = "/players"
        params = {"name": name, "limit": limit}
        
        await _wait_for_rate_limit()
        logger.info("Searching players", name=name)
        response = await self._get(endpoint, params=params)
        return response.get("results", [])
    
    async def get_heroes(self) -> list[dict[str, Any]]:
        """Get list of all heroes.
        
        Returns:
            List of hero data
        """
        await _wait_for_rate_limit()
        endpoint = "/heroes"
        response = await self._get(endpoint)
        return response
    
    async def get_maps(self) -> list[dict[str, Any]]:
        """Get list of all maps.
        
        Returns:
            List of map data
        """
        await _wait_for_rate_limit()
        endpoint = "/maps"
        response = await self._get(endpoint)
        return response
    
    async def check_health(self) -> bool:
        """Check if the API is healthy.
        
        Returns:
            True if API is healthy
        """
        try:
            await self._get("/")
            return True
        except Exception:
            return False

