"""World-switching domain logic: pure, testable, no I/O.

The cog owns Discord I/O and the GCE orchestration (set metadata, stop,
start). The *rules* -- what a legal world name is, and how to reconcile
the live daemon reading against the instance metadata and the on-disk
cache -- live here so they can be unit-tested without mocking discord.py
or GCE, matching the pattern in src.utils.checks.is_allowed_channel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.services.server_query import LiveStatus
from src.services.world_cache import CachedWorlds

MAX_WORLD_NAME_LEN = 32

# WORLD_NAME flows into a filename (worlds_local/<name>/ or <name>.db),
# world.env, an instance metadata value, and a shell heredoc line. Keep
# it to a boring, escaping-free charset that's also a legal Valheim world
# name: start alnum, then alnum plus . _ - (the "1.0" world needs the dot).
_WORLD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_world_name(name: str) -> str | None:
    """Return a human-readable error if `name` is illegal, else None."""
    if not name or not name.strip():
        return "World name can't be empty."
    if name != name.strip():
        return "World name can't have leading or trailing spaces."
    if len(name) > MAX_WORLD_NAME_LEN:
        return f"World name is too long (max {MAX_WORLD_NAME_LEN} characters)."
    if name in (".", ".."):
        return "World name can't be `.` or `..`."
    if not _WORLD_NAME_RE.match(name):
        return (
            "World name must start with a letter or digit and use only "
            "letters, digits, dots, underscores, or hyphens."
        )
    return None


def _is_real_world(name: str) -> bool:
    """False for lloesche backup artifacts that share the worlds_local dir.

    Backups are `<world>_backup_auto-<ts>` dirs (1.0 format) or
    `<world>_backup_*` / `*.old` files (pre-1.0). They must never appear
    as switchable worlds. The daemon already filters these, but we also
    filter here so a stale cache (or any future producer) can't surface one.
    """
    return "_backup_" not in name and not name.endswith(".old")


def _real_worlds(names: list[str]) -> list[str]:
    return sorted(n for n in names if _is_real_world(n))


@dataclass(frozen=True)
class Inventory:
    """Reconciled view of the world inventory for rendering + validation."""

    worlds: list[str]
    active: str | None
    live: bool  # True = from the running daemon (ground truth), False = from cache
    updated: str | None  # cache write time, meaningful only when live is False


def resolve_inventory(
    live: LiveStatus | None,
    metadata_active: str | None,
    cached: CachedWorlds,
) -> Inventory:
    """Reconcile the live daemon reading, instance metadata, and the cache.

    - World list: the running daemon's disk scan is ground truth; fall
      back to the cache when the VM is off (or the daemon hasn't answered).
    - Active world: the instance `world-name` metadata is authoritative and
      readable even when the VM is off, so it wins; then the daemon's
      world.env reading; then the cached value.
    """
    if live is not None and live.server_running and live.worlds:
        active = metadata_active or live.active_world or cached.active
        return Inventory(worlds=_real_worlds(live.worlds), active=active, live=True, updated=None)

    active = metadata_active or cached.active
    return Inventory(
        worlds=_real_worlds(cached.worlds), active=active, live=False, updated=cached.updated
    )
