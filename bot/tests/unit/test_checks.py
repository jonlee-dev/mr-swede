"""Unit tests for src.utils.checks.

Tests the pure-logic `is_allowed_channel` directly. The discord.py
glue (`requires_channel`) is exercised only at integration time.
"""

import pytest

from src.utils.checks import is_allowed_channel


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
