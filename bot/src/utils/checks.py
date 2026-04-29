"""Slash-command checks (`app_commands.check` predicates).

Pattern:
    Pure-logic predicate (`is_allowed_channel`, etc.) is exported and
    unit-tested without any discord.py mocking. The discord.py wrapper
    (`requires_channel`) handles I/O -- ephemeral redirect message and
    raising CheckFailure -- and is intentionally thin so the testable
    surface stays in the pure functions.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

import discord
from discord import app_commands

from src.config.logging import get_logger

logger = get_logger(__name__)


def is_allowed_channel(actual_channel_id: int | None, configured_id: str) -> bool:
    """Return True if a command invoked from `actual_channel_id` should run.

    Pure logic so tests don't need to mock discord.Interaction.

    Rules:
      - Empty `configured_id` (or just whitespace) means no restriction --
        the command runs from anywhere. This is the default for cog
        instances that don't pin a channel.
      - Otherwise the actual channel ID must match the configured one.
        Comparison is by string form so we don't care whether one side
        is `int` and the other is the env-var string GCP / Cloud Run
        hands us.
      - DMs (actual_channel_id=None) never satisfy a non-empty
        configured_id -- this prevents a slash command from running in
        a DM bypass.
    """
    if not configured_id or not configured_id.strip():
        return True
    if actual_channel_id is None:
        return False
    return str(actual_channel_id) == configured_id.strip()


def requires_channel(channel_id_attr: str) -> Callable[[Any], Any]:
    """Slash-command decorator: only run when invoked from a configured channel.

    `channel_id_attr` is the name of a `Settings` field whose value is
    the allowed channel ID (string). Empty string in Settings = no
    restriction (the command runs anywhere).

    On rejection, the decorator sends an ephemeral redirect telling the
    user where to invoke instead, then raises `app_commands.CheckFailure`
    to abort the command. The bot's existing app-command error handler
    branches on `CheckFailure` and skips its generic "an error occurred"
    fallback.
    """
    # Imported lazily so test code that imports this module doesn't pay
    # the cost of pydantic-settings init.
    from src.config.settings import get_settings

    async def predicate(interaction: discord.Interaction) -> bool:
        settings = get_settings()
        configured_id = getattr(settings, channel_id_attr, "")
        if is_allowed_channel(interaction.channel_id, configured_id):
            return True

        logger.info(
            "Channel check rejected interaction",
            command=interaction.command.name if interaction.command else "unknown",
            user=str(interaction.user),
            actual_channel_id=interaction.channel_id,
            configured_id=configured_id,
        )

        # Send the user a useful redirect, then raise so the command
        # body never runs. We send-message-not-followup because no
        # defer has happened yet at the check stage.
        # `discord.NotFound` swallowed: interaction expired in the
        # time between the user invoking and us getting here.
        with contextlib.suppress(discord.NotFound):
            await interaction.response.send_message(
                f"This command only works in <#{configured_id}>.",
                ephemeral=True,
            )

        raise app_commands.CheckFailure(
            f"channel {interaction.channel_id} not allowed for {channel_id_attr}"
        )

    return app_commands.check(predicate)
