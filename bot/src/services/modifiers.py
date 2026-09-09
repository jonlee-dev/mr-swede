"""World-modifier domain logic: pure, testable, no I/O.

Valheim 1.0 dedicated-server world modifiers are set via launch args:
    -modifier <key> <value>   the five difficulty dials
    -preset <name>            a difficulty bundle
    -setkey <key>             global-key toggles (no-build-cost, etc.)

The bot stores the full args string in the `world-modifiers` instance
metadata; the VM startup-script passes it straight through as SERVER_ARGS.
This module parses that string into structure, formats it back, validates
keys/values, and applies edits -- so the cog stays thin and everything
here is unit-testable without discord.py or GCE (matches the pattern in
src.utils.checks and src.services.worlds).

The curated global-key set is the standard in-game world-modifier menu
toggles. Valheim accepts other global keys via -setkey, but exposing the
whole namespace (which includes progression keys) would be a footgun.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# key -> valid values, in rough mild->harsh order. "Unset" means the game
# default (shown as "default"); passing the sentinel "default" clears it.
MODIFIERS: dict[str, list[str]] = {
    "combat": ["veryeasy", "easy", "hard", "veryhard"],
    "deathpenalty": ["casual", "veryeasy", "easy", "hard", "hardcore"],
    "resources": ["muchless", "less", "more", "muchmore", "most"],
    "raids": ["none", "muchless", "less", "more", "muchmore"],
    "portals": ["casual", "hard", "veryhard"],
}

PRESETS: list[str] = ["normal", "casual", "easy", "hard", "hardcore", "immersive", "hammer"]

# Global-key toggles exposed by the world-modifier menu, with display labels.
GLOBAL_KEYS: dict[str, str] = {
    "nobuildcost": "No build cost",
    "nomap": "No map",
    "noportals": "No portals",
    "passivemobs": "Passive mobs",
    "playerevents": "Player-based events",
}

CLEAR_VALUE = "default"  # sentinel for `set <key> default` -> clear the dial


@dataclass(frozen=True)
class ServerModifiers:
    """Structured view of the SERVER_ARGS modifier string."""

    modifiers: dict[str, str] = field(default_factory=dict)  # subset of MODIFIERS
    preset: str | None = None
    keys: tuple[str, ...] = ()  # enabled global keys (subset of GLOBAL_KEYS)

    def to_args(self) -> str:
        """Render back to a `-preset ... -modifier k v ... -setkey k ...` string."""
        parts: list[str] = []
        if self.preset and self.preset != "normal":
            parts.append(f"-preset {self.preset}")
        for key in MODIFIERS:  # stable, menu order
            if key in self.modifiers:
                parts.append(f"-modifier {key} {self.modifiers[key]}")
        for gk in GLOBAL_KEYS:  # stable order
            if gk in self.keys:
                parts.append(f"-setkey {gk}")
        return " ".join(parts)


def parse_args(s: str | None) -> ServerModifiers:
    """Parse a SERVER_ARGS string into ServerModifiers, ignoring anything
    unrecognized (so a stray non-modifier flag can't crash the parse)."""
    toks = (s or "").split()
    mods: dict[str, str] = {}
    preset: str | None = None
    keys: list[str] = []
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        if t == "-modifier" and i + 2 < n:
            key, val = toks[i + 1].lower(), toks[i + 2].lower()
            if key in MODIFIERS:
                mods[key] = val
            i += 3
        elif t == "-preset" and i + 1 < n:
            preset = toks[i + 1].lower()
            i += 2
        elif t == "-setkey" and i + 1 < n:
            gk = toks[i + 1].lower()
            if gk not in keys:
                keys.append(gk)
            i += 2
        else:
            i += 1
    return ServerModifiers(modifiers=mods, preset=preset, keys=tuple(keys))


# --- validation (return an error string, or None if ok) ---


def validate_modifier(key: str, value: str) -> str | None:
    if key not in MODIFIERS:
        return f"Unknown modifier `{key}`. Valid: {', '.join(MODIFIERS)}."
    if value != CLEAR_VALUE and value not in MODIFIERS[key]:
        return (
            f"Invalid value `{value}` for `{key}`. "
            f"Valid: {', '.join(MODIFIERS[key])} (or `default` to clear)."
        )
    return None


def validate_preset(name: str) -> str | None:
    if name not in PRESETS:
        return f"Unknown preset `{name}`. Valid: {', '.join(PRESETS)}."
    return None


def validate_key(name: str) -> str | None:
    if name not in GLOBAL_KEYS:
        return f"Unknown toggle `{name}`. Valid: {', '.join(GLOBAL_KEYS)}."
    return None


# --- pure edits (return a NEW ServerModifiers) ---


def set_modifier(m: ServerModifiers, key: str, value: str) -> ServerModifiers:
    d = dict(m.modifiers)
    if value == CLEAR_VALUE:
        d.pop(key, None)
    else:
        d[key] = value
    return replace(m, modifiers=d)


def set_preset(m: ServerModifiers, name: str | None) -> ServerModifiers:
    return replace(m, preset=(None if name in (None, "normal") else name))


def set_key(m: ServerModifiers, gk: str, enabled: bool) -> ServerModifiers:
    keys = [k for k in m.keys if k != gk]
    if enabled:
        keys.append(gk)
    return replace(m, keys=tuple(keys))


def clear_all() -> ServerModifiers:
    return ServerModifiers()
