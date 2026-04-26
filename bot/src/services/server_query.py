"""Steam A2S query: ask a running Valheim server for player count and uptime.

Used by /valheim status when the VM is RUNNING. If the VM is TERMINATED or
the query times out, the cog falls back to reporting just the GCE state.

Phase 3: implement with python-a2s (or hand-rolled UDP socket against
port 2457 -- the Steam query port, which is always game_port + 1).
"""

from dataclasses import dataclass

from src.config.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class GameState:
    """A snapshot of in-game state from the A2S query."""

    server_name: str
    map_name: str  # World name
    player_count: int
    max_players: int


def query(host: str, port: int = 2457, timeout: float = 3.0) -> GameState | None:
    """Query the Valheim server. Returns None on timeout/error.

    Args:
        host: Public IP of the VM.
        port: Steam query port. Valheim uses game_port + 1, default 2457.
        timeout: Seconds to wait before giving up.
    """
    raise NotImplementedError("Phase 3: implement with python-a2s")
