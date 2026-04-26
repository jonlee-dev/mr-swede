"""Steam A2S query: ask a running Valheim server for player count and map name.

Used by /valheim status when the VM is RUNNING. If the VM is TERMINATED or
the query fails (timeout, connection refused, malformed response), the cog
falls back to reporting just the GCE state — `query()` swallows errors and
returns None to make that fallback path trivial.

Valheim's A2S port is `game_port + 1`. The default game port is 2456, so
the query port is 2457.
"""

import asyncio
from dataclasses import dataclass

import a2s

from src.config.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class GameState:
    """A snapshot of in-game state from the A2S query."""

    server_name: str
    map_name: str  # World name
    player_count: int
    max_players: int


async def query(host: str, port: int = 2457, timeout: float = 3.0) -> GameState | None:
    """Query the Valheim server. Returns None on any error.

    Returning None (instead of raising) is intentional: the caller renders
    "VM up but game not reachable" the same way regardless of *why* the
    query failed, so distinguishing failure modes here would be noise.
    """

    def _query() -> GameState | None:
        try:
            info = a2s.info((host, port), timeout=timeout)
        except Exception as e:
            logger.debug("a2s query failed", host=host, port=port, error=str(e))
            return None
        return GameState(
            server_name=info.server_name,
            map_name=info.map_name,
            player_count=info.player_count,
            max_players=info.max_players,
        )

    return await asyncio.to_thread(_query)
