"""Pure embed renderers shared by the cogs.

Each function takes a service-layer dataclass (TrackInfo, PlayResult,
InstanceState, LiveStatus) and returns a `discord.Embed`. No I/O, no
mutation, no side effects -- the embeds module is a presentation
adapter and stays trivially testable.

Function naming convention:
    <feature>_<what>_embed(...)
so callers can `from src.cogs.embeds import music_track_embed` without
ambiguity if a future feature also has a "track" concept.
"""

from __future__ import annotations

import discord

from src.services.compute import InstanceState
from src.services.modifiers import GLOBAL_KEYS, MODIFIERS, ServerModifiers
from src.services.music import PLAYLIST_TRACK_CAP, PlayResult, TrackInfo, format_duration
from src.services.server_query import LiveStatus


_MUSIC_DEFAULT_COLOR = 0x1ABC9C


def music_track_embed(
    track: TrackInfo, header: str, color: int = _MUSIC_DEFAULT_COLOR
) -> discord.Embed:
    """Single-track "Now playing" / "Queued (#N)" embed."""
    embed = discord.Embed(title=header, description=f"**{track.title}**", color=color)
    embed.add_field(name="Artist", value=track.author, inline=True)
    embed.add_field(name="Length", value=format_duration(track.duration_ms), inline=True)
    if track.requester_id is not None:
        embed.add_field(name="Requested by", value=f"<@{track.requester_id}>", inline=True)
    if track.uri:
        embed.url = track.uri
    return embed


def music_playlist_embed(
    result: PlayResult, color: int = _MUSIC_DEFAULT_COLOR
) -> discord.Embed:
    """Summary embed for a playlist/album URL resolution.

    Surfaces:
      - total tracks queued (= 1 first_track + extra_tracks_queued)
      - playlist title (or "playlist" fallback when lavasrc surfaces no name)
      - truncation warning when the source playlist exceeded PLAYLIST_TRACK_CAP
      - unresolved count when some tracks couldn't be matched
      - first-up track inline so the user sees what's playing now
    """
    assert result.first_track is not None  # caller checks
    total_queued = 1 + result.extra_tracks_queued
    title = result.playlist_title or "playlist"
    embed = discord.Embed(
        title=f"Queued {total_queued} tracks",
        description=f'From **"{title}"**',
        color=color,
    )

    embed.add_field(
        name="First up",
        value=f"**{result.first_track.title}** "
        f"({format_duration(result.first_track.duration_ms)})",
        inline=False,
    )

    if result.truncated_from is not None:
        embed.add_field(
            name="Truncated",
            value=(
                f"Playlist had {result.truncated_from} tracks; "
                f"queued the first {total_queued} (cap = {PLAYLIST_TRACK_CAP})."
            ),
            inline=False,
        )

    if result.unresolved_count > 0:
        embed.add_field(
            name="Unresolved",
            value=f"{result.unresolved_count} track(s) couldn't be resolved and were skipped.",
            inline=False,
        )

    if result.first_track.requester_id is not None:
        embed.set_footer(text=f"Requested by user {result.first_track.requester_id}")

    return embed


_VALHEIM_STATUS_COLORS: dict[str, int] = {
    "RUNNING": 0x2ECC71,  # green
    "PROVISIONING": 0xF1C40F,  # amber
    "STAGING": 0xF1C40F,
    "STOPPING": 0xE67E22,  # orange
    "TERMINATED": 0x95A5A6,  # grey
}


def valheim_status_embed(
    state: InstanceState,
    live: LiveStatus | None,
    password: str | None,
) -> discord.Embed:
    """Render an InstanceState + optional LiveStatus + password into a
    Discord embed. Pure function; the cog calls it after gathering the
    inputs.
    """
    color = _VALHEIM_STATUS_COLORS.get(state.status, 0x3498DB)
    embed = discord.Embed(title=f"Valheim — {state.status}", color=color)
    embed.add_field(name="Instance", value=f"`{state.name}` ({state.machine_type})", inline=False)
    embed.add_field(name="Zone", value=state.zone, inline=True)
    if state.public_ip:
        embed.add_field(name="Address", value=f"`{state.public_ip}:2456`", inline=True)

    if live is not None and live.server_running:
        if live.join_code:
            # PlayFab/crossplay path: 6-digit code in Valheim's "Join Game" tab.
            embed.add_field(name="Join code", value=f"`{live.join_code}`", inline=True)
        else:
            # Steam-only path (CROSSPLAY=false): no join code exists.
            # Surface the menu path so first-time joiners aren't lost.
            embed.add_field(
                name="How to join",
                value="Valheim → **Join Game** → **Join IP** → paste the address above",
                inline=False,
            )
        embed.add_field(name="Players", value=str(live.player_count), inline=True)
        if password:
            embed.add_field(name="Password", value=f"`{password}`", inline=True)
    elif state.status == "RUNNING":
        embed.add_field(
            name="Game server",
            value="VM is up but the game server isn't answering yet (Valheim takes "
            "~60-90s to boot after the VM does). Try `/valheim status` again shortly.",
            inline=False,
        )

    return embed


def valheim_worlds_embed(
    worlds: list[str],
    active: str | None,
    *,
    live: bool,
    updated: str | None = None,
) -> discord.Embed:
    """Render the world inventory for `/valheim world list`.

    `live` distinguishes a fresh reading from the running server (green,
    authoritative) from a cached one served while the VM is off (grey,
    with a freshness hint). Pure function; the cog resolves the inputs.
    """
    color = 0x2ECC71 if live else 0x95A5A6
    embed = discord.Embed(title="Valheim worlds", color=color)

    if not worlds:
        embed.description = (
            "No worlds found on the server yet."
            if live
            else "No cached world list yet — run `/valheim status` or `/valheim world list` "
            "once while the server is up, and it'll be remembered for next time."
        )
        return embed

    embed.description = "\n".join(
        f"• `{w}`{'  ◀ **active**' if w == active else ''}" for w in worlds
    )

    # The active world can be set (in metadata) but not yet exist on disk
    # -- e.g. right after `/valheim world new` before the first boot.
    if active and active not in worlds:
        embed.add_field(
            name="Active",
            value=f"`{active}` — will be generated on next boot",
            inline=False,
        )

    embed.set_footer(
        text="live from the server"
        if live
        else f"server offline — cached list{f' from {updated}' if updated else ''}"
    )
    return embed


def valheim_modifiers_embed(mods: ServerModifiers) -> discord.Embed:
    """Render the world modifiers for `/valheim modifier list`.

    Shows all five difficulty dials (value or `default`), the active
    preset if any, and which global-key toggles are on. Pure function.
    """
    embed = discord.Embed(title="Valheim world modifiers", color=0x8E7CC3)

    if mods.preset and mods.preset != "normal":
        embed.add_field(name="Preset", value=f"`{mods.preset}`", inline=False)

    embed.add_field(
        name="Difficulty",
        value="\n".join(f"• **{key}**: `{mods.modifiers.get(key, 'default')}`" for key in MODIFIERS),
        inline=False,
    )

    on = [label for gk, label in GLOBAL_KEYS.items() if gk in mods.keys]
    embed.add_field(
        name="Toggles",
        value=("• " + "\n• ".join(on)) if on else "_none_",
        inline=False,
    )

    embed.set_footer(text="changes apply on the next server restart")
    return embed


__all__ = [
    "music_playlist_embed",
    "music_track_embed",
    "valheim_modifiers_embed",
    "valheim_status_embed",
    "valheim_worlds_embed",
]
