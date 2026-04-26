"""Base HTTP client for API services."""

from typing import Any

import httpx

from src.config.logging import get_logger

logger = get_logger(__name__)


class BaseAPIClient:
    """Base class for API clients with common HTTP functionality."""
    
    def __init__(
        self, 
        base_url: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ):
        """Initialize the API client.
        
        Args:
            base_url: Base URL for the API
            timeout: Request timeout in seconds
            headers: Default headers for all requests
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._default_headers = headers or {}
        self._client: httpx.AsyncClient | None = None
    
    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            # Explicitly disable retries - APIs have strict rate limits
            transport = httpx.AsyncHTTPTransport(retries=0)
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=self._default_headers,
                transport=transport,
            )
        return self._client
    
    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def _get(
        self, 
        endpoint: str, 
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make a GET request.
        
        Args:
            endpoint: API endpoint
            params: Query parameters
            headers: Additional headers
            
        Returns:
            JSON response data
            
        Raises:
            httpx.HTTPStatusError: On HTTP errors
        """
        response = await self.client.get(
            endpoint,
            params=params,
            headers=headers,
        )
        
        # Log rate limit headers if present (useful for debugging 429s)
        if response.status_code == 429:
            rate_limit_headers = {
                k: v for k, v in response.headers.items()
                if "rate" in k.lower() or "retry" in k.lower() or "limit" in k.lower()
            }
            logger.warning(
                "Rate limited (429)",
                endpoint=endpoint,
                rate_limit_headers=rate_limit_headers or "none",
                retry_after=response.headers.get("Retry-After"),
            )
        
        response.raise_for_status()
        return response.json()
    
    async def _post(
        self,
        endpoint: str,
        data: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make a POST request.
        
        Args:
            endpoint: API endpoint
            data: Form data
            json_data: JSON body
            headers: Additional headers
            
        Returns:
            JSON response data
            
        Raises:
            httpx.HTTPStatusError: On HTTP errors
        """
        response = await self.client.post(
            endpoint,
            data=data,
            json=json_data,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


class OAuthClient(BaseAPIClient):
    """API client with OAuth2 token management."""
    
    def __init__(
        self,
        base_url: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        **kwargs: Any,
    ):
        """Initialize OAuth client.
        
        Args:
            base_url: Base URL for the API
            token_url: OAuth token endpoint
            client_id: OAuth client ID
            client_secret: OAuth client secret
            **kwargs: Additional arguments for BaseAPIClient
        """
        super().__init__(base_url, **kwargs)
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token: str | None = None
        self._token_expires_at: float = 0
    
    async def get_token(self) -> str:
        """Get or refresh the access token.
        
        Returns:
            Valid access token
        """
        import time
        
        # Return cached token if still valid (with 60s buffer)
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token
        
        # Request new token (no retries - APIs have strict rate limits)
        transport = httpx.AsyncHTTPTransport(retries=0)
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post(
                self.token_url,
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
            )
            response.raise_for_status()
            data = response.json()
        
        self._access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        self._token_expires_at = time.time() + expires_in
        
        logger.debug("Refreshed OAuth token", expires_in=expires_in)
        return self._access_token
    
    async def _get_with_auth(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated GET request.
        
        Args:
            endpoint: API endpoint
            params: Query parameters
            
        Returns:
            JSON response data
        """
        token = await self.get_token()
        return await self._get(
            endpoint,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )

