"""Fetch live server state from the VM's status-server daemon.

The VM runs a small Python HTTP server (server/scripts/status-server.py)
that periodically queries the Valheim dedicated server's Steam A2S
endpoint (UDP localhost:2457) and serves the parsed result as JSON
at GET :9001/status.json.

Why A2S now (this used to say "why log scraping")?
    Two earlier daemon implementations parsed `docker compose logs`
    and both lost player events under load (the 2026-05-02 truncation
    bug and the 2026-05-03 follow-stream fragility bug). The current
    daemon queries the game itself via the Steam Server Browser
    protocol, which is the authoritative source for player count.
    The old objection to A2S was Valheim's crossplay/PlayFab transport
    making queries unreliable; with `CROSSPLAY=false` (2026-05-02
    decision in docs/prd.md), the protocol responds correctly.

The endpoint returns:
    {
      "last_update": "<ISO8601 UTC>",
      "join_code": null,           # always null now -- A2S doesn't
                                   # expose PlayFab join codes, and
                                   # CROSSPLAY=false has no PlayFab
      "player_count": <int>,
      "server_running": <bool>,    # true iff most recent A2S query
                                   # succeeded
      "error": "<repr>"            # only when the last query failed
    }
"""

from dataclasses import dataclass

import httpx

from src.config.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class LiveStatus:
    """A snapshot of live server state from the status-server daemon."""

    join_code: str | None
    player_count: int
    server_running: bool
    last_update: str | None  # ISO8601, useful for stale-data detection in the embed


async def fetch_status(host: str, port: int = 9001, timeout: float = 5.0) -> LiveStatus | None:
    """Fetch and parse /status.json. Returns None on any failure.

    Returning None (instead of raising) is intentional: the caller
    renders "VM up but status server isn't answering yet" the same way
    regardless of *why* the fetch failed, so distinguishing failure
    modes here would be noise. Specific failures still get logged.
    """
    url = f"http://{host}:{port}/status.json"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError) as e:
        logger.debug("status-server fetch failed", url=url, error=str(e))
        return None
    except Exception as e:
        logger.warning("status-server response not parseable", url=url, error=str(e))
        return None

    # The daemon includes an `error` field when its A2S query failed.
    # Treat that as "stale data" -- still pass through what we have,
    # but log so debug runs surface the issue. The watcher's probe
    # function is the canonical consumer that needs to act on this
    # field; the bot's status embed is a softer surface and just
    # renders the last known good values with a freshness hint.
    if data.get("error"):
        logger.info(
            "status-server reports query error",
            error=data["error"],
            last_update=data.get("last_update"),
        )

    return LiveStatus(
        join_code=data.get("join_code"),
        player_count=int(data.get("player_count") or 0),
        server_running=bool(data.get("server_running")),
        last_update=data.get("last_update"),
    )
