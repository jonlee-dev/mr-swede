"""Blizzard/Battle.net API client.

This client handles OAuth authentication and provides access to Blizzard's APIs.
Note: Direct Overwatch stats are not available via Blizzard API - use Overfast API instead.
This client is useful for:
- Account linking/validation
- Hearthstone deck decoding
- Other Blizzard game data
"""

from typing import Any

from src.config.logging import get_logger
from src.config.secrets import get_secrets
from src.config.settings import get_settings
from src.services.base import OAuthClient

logger = get_logger(__name__)


class BlizzardClient(OAuthClient):
    """Client for Blizzard's Battle.net API."""
    
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        region: str | None = None,
    ) -> None:
        """Initialize Blizzard API client.
        
        Args:
            client_id: Blizzard API client ID (loaded from secrets if not provided)
            client_secret: Blizzard API client secret (loaded from secrets if not provided)
            region: API region (us, eu, kr, tw, cn)
        """
        settings = get_settings()
        secrets = get_secrets()
        
        self.region = region or settings.blizzard_region
        
        # Get credentials from secrets or parameters
        if client_id and client_secret:
            _client_id = client_id
            _client_secret = client_secret
        elif secrets.blizzard:
            _client_id = secrets.blizzard.client_id
            _client_secret = secrets.blizzard.client_secret
        elif settings.blizzard_client_id and settings.blizzard_client_secret:
            _client_id = settings.blizzard_client_id
            _client_secret = settings.blizzard_client_secret.get_secret_value()
        else:
            raise ValueError(
                "Blizzard credentials not found. "
                "Configure in GSM or set BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET env vars."
            )
        
        super().__init__(
            base_url=f"https://{self.region}.api.blizzard.com",
            token_url=f"https://{self.region}.battle.net/oauth/token",
            client_id=_client_id,
            client_secret=_client_secret,
        )
    
    # ==================== Hearthstone API ====================
    
    async def get_hearthstone_deck(self, deck_code: str) -> dict[str, Any]:
        """Decode a Hearthstone deck code.
        
        Args:
            deck_code: Hearthstone deck code string
            
        Returns:
            Decoded deck information
        """
        endpoint = "/hearthstone/deck"
        params = {
            "locale": "en_US",
            "code": deck_code,
        }
        
        logger.info("Decoding Hearthstone deck")
        return await self._get_with_auth(endpoint, params=params)
    
    async def get_hearthstone_card(self, card_id: int | str) -> dict[str, Any]:
        """Get Hearthstone card information.
        
        Args:
            card_id: Card ID or slug
            
        Returns:
            Card information
        """
        endpoint = f"/hearthstone/cards/{card_id}"
        params = {"locale": "en_US"}
        
        return await self._get_with_auth(endpoint, params=params)
    
    async def search_hearthstone_cards(
        self,
        name: str | None = None,
        card_class: str | None = None,
        mana_cost: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Search Hearthstone cards.
        
        Args:
            name: Card name to search
            card_class: Filter by class
            mana_cost: Filter by mana cost
            **kwargs: Additional filter parameters
            
        Returns:
            Search results
        """
        endpoint = "/hearthstone/cards"
        params: dict[str, Any] = {"locale": "en_US"}
        
        if name:
            params["textFilter"] = name
        if card_class:
            params["class"] = card_class
        if mana_cost is not None:
            params["manaCost"] = mana_cost
        params.update(kwargs)
        
        return await self._get_with_auth(endpoint, params=params)
    
    # ==================== Account Validation ====================
    
    async def validate_battle_tag_format(self, battle_tag: str) -> bool:
        """Validate BattleTag format.
        
        Args:
            battle_tag: BattleTag to validate
            
        Returns:
            True if format is valid
        """
        import re
        # BattleTag format: Name#1234 (2-12 chars name, 4-8 digit discriminator)
        pattern = r"^[a-zA-Z][a-zA-Z0-9]{1,11}#\d{4,8}$"
        return bool(re.match(pattern, battle_tag))
    
    # ==================== WoW API (example of other Blizzard APIs) ====================
    
    async def get_wow_character(
        self, 
        realm_slug: str, 
        character_name: str,
    ) -> dict[str, Any]:
        """Get WoW character profile.
        
        Args:
            realm_slug: Realm slug
            character_name: Character name (lowercase)
            
        Returns:
            Character profile data
        """
        endpoint = f"/profile/wow/character/{realm_slug}/{character_name.lower()}"
        params = {"namespace": f"profile-{self.region}", "locale": "en_US"}
        
        return await self._get_with_auth(endpoint, params=params)
    
    # ==================== Utility Methods ====================
    
    async def check_health(self) -> bool:
        """Check if the API and credentials are working.
        
        Returns:
            True if healthy
        """
        try:
            await self.get_token()
            return True
        except Exception as e:
            logger.error("Blizzard API health check failed", error=str(e))
            return False


def get_blizzard_client() -> BlizzardClient | None:
    """Get a Blizzard client if credentials are available.
    
    Returns:
        BlizzardClient or None if credentials not configured
    """
    try:
        return BlizzardClient()
    except ValueError:
        logger.warning("Blizzard client not available - credentials not configured")
        return None
