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


__all__ = [
    "music_playlist_embed",
    "music_track_embed",
    "valheim_status_embed",
]
