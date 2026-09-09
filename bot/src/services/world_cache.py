"""Persistent cache of the Valheim world inventory.

The status daemon on the VM only answers while the VM is RUNNING, but
`/valheim world list` should also work while it's off (to browse names
before switching). So whenever the bot reads the world list live from the
daemon it writes it here; when the daemon is unreachable, the cog falls
back to this cache and labels it as such.

Deliberately crash-proof I/O: a missing or corrupt cache file yields an
empty snapshot rather than raising -- a cold cache is a normal state
(fresh bot host, first run), not an error worth surfacing to the user.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.config.logging import get_logger
from src.config.settings import get_settings

logger = get_logger(__name__)


@dataclass(frozen=True)
class CachedWorlds:
    """A point-in-time snapshot of the world inventory as last seen live."""

    worlds: list[str] = field(default_factory=list)
    active: str | None = None
    updated: str | None = None  # ISO8601 timestamp of the last write

    @property
    def is_empty(self) -> bool:
        return not self.worlds


def _path() -> str:
    return os.path.expanduser(get_settings().valheim_world_cache_path)


def remember(worlds: list[str], active: str | None) -> None:
    """Persist the latest world inventory. Never raises.

    Written atomically (tmp + os.replace) so a crash mid-write can't
    leave a truncated file that the next recall() would reject.
    """
    path = _path()
    payload = {
        "worlds": sorted(worlds),
        "active": active,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("world cache write failed", path=path, error=str(exc))


def recall() -> CachedWorlds:
    """Load the cached inventory, or an empty snapshot if absent/corrupt."""
    path = _path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return CachedWorlds()
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("world cache read failed", path=path, error=str(exc))
        return CachedWorlds()

    raw = data.get("worlds") or []
    worlds = [str(w) for w in raw] if isinstance(raw, list) else []
    return CachedWorlds(worlds=worlds, active=data.get("active"), updated=data.get("updated"))
