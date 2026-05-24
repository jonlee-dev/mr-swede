"""Unit tests for src.utils.checks.

Tests the pure-logic `is_allowed_channel` directly. The discord.py
glue (`requires_channel`) is exercised only at integration time.
`requires_guild` is exercised here via a minimal interaction mock
since its job IS the I/O dance (defer + ephemeral redirect).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.utils.checks import is_allowed_channel, requires_guild


class TestIsAllowedChannel:
    def test_empty_config_allows_anywhere(self):
        # No restriction configured -- any channel is fine.
        assert is_allowed_channel(actual_channel_id=12345, configured_id="") is True

    def test_whitespace_config_allows_anywhere(self):
        # Operators sometimes leave " " in env vars; treat as unset.
        assert is_allowed_channel(12345, "   ") is True

    def test_configured_id_matches(self):
        assert is_allowed_channel(12345, "12345") is True

    def test_configured_id_does_not_match(self):
        assert is_allowed_channel(12345, "99999") is False

    def test_string_int_comparison(self):
        # Channel IDs come in as int from discord.py and as str from
        # env vars; comparison normalizes via str().
        assert is_allowed_channel(actual_channel_id=12345, configured_id="12345") is True

    def test_dm_rejected_when_restricted(self):
        # actual_channel_id=None happens when an interaction is from
        # a DM context. The bot doesn't accept DMs for music, so
        # restriction must reject.
        assert is_allowed_channel(actual_channel_id=None, configured_id="12345") is False

    def test_dm_allowed_when_unrestricted(self):
        # Mirror image: when no restriction is set, DMs DO satisfy.
        # We don't currently allow DMs for any feature, but this
        # behavior keeps the pure check from making policy on its own.
        assert is_allowed_channel(actual_channel_id=None, configured_id="") is True

    def test_zero_channel_id_does_not_match_unrestricted(self):
        # 0 is a falsy int but NOT None. Make sure we still treat it
        # as a real channel ID (i.e. compare to the configured value).
        assert is_allowed_channel(actual_channel_id=0, configured_id="0") is True
        assert is_allowed_channel(actual_channel_id=0, configured_id="1") is False

    @pytest.mark.parametrize(
        ("actual", "configured", "expected"),
        [
            (123, "123", True),
            (123, " 123", True),  # leading whitespace stripped
            (123, "123 ", True),  # trailing whitespace stripped
            (123, " 123 ", True),
            (123, "1234", False),
            (1234, "123", False),  # not a substring match
        ],
    )
    def test_whitespace_tolerance(self, actual, configured, expected):
        assert is_allowed_channel(actual, configured) is expected


def _interaction(guild: object | None) -> MagicMock:
    """Minimal Interaction stand-in for @requires_guild branches."""
    inter = MagicMock()
    inter.guild = guild
    inter.response.defer = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter


class TestRequiresGuild:
    """One test per branch + a sanity check that the inner body actually
    receives the interaction kwargs."""

    async def test_defers_and_invokes_body_when_guild_present(self):
        called_with: list[tuple] = []

        class Cog:
            @requires_guild
            async def cmd(self, interaction, *, query):
                called_with.append((interaction, query))
                return "ok"

        cog = Cog()
        inter = _interaction(guild=object())
        result = await cog.cmd(inter, query="hello")

        assert result == "ok"
        inter.response.defer.assert_awaited_once_with(thinking=True)
        inter.followup.send.assert_not_called()
        assert called_with == [(inter, "hello")]

    async def test_dm_invocation_short_circuits_with_ephemeral_message(self):
        body_called = False

        class Cog:
            @requires_guild
            async def cmd(self, interaction):
                nonlocal body_called
                body_called = True

        cog = Cog()
        inter = _interaction(guild=None)
        result = await cog.cmd(inter)

        # Decorator returns None on short-circuit. Body never runs.
        assert result is None
        assert body_called is False
        inter.response.defer.assert_awaited_once_with(thinking=True)
        inter.followup.send.assert_awaited_once()
        args, kwargs = inter.followup.send.call_args
        assert "server channel" in args[0]
        assert kwargs.get("ephemeral") is True
