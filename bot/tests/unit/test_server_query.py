"""Unit tests for src.services.server_query."""

import socket
from unittest.mock import MagicMock, patch

from src.services.server_query import GameState, query


def _fake_a2s_info(
    server_name: str = "Midgard",
    map_name: str = "Midgard",
    player_count: int = 2,
    max_players: int = 10,
):
    info = MagicMock()
    info.server_name = server_name
    info.map_name = map_name
    info.player_count = player_count
    info.max_players = max_players
    return info


class TestQuery:
    async def test_returns_game_state_on_success(self):
        with patch(
            "src.services.server_query.a2s.info",
            return_value=_fake_a2s_info(server_name="Mr. Swede", player_count=3),
        ):
            state = await query("1.2.3.4")
        assert state == GameState(
            server_name="Mr. Swede",
            map_name="Midgard",
            player_count=3,
            max_players=10,
        )

    async def test_returns_none_on_timeout(self):
        with patch(
            "src.services.server_query.a2s.info",
            side_effect=socket.timeout("timed out"),
        ):
            assert await query("1.2.3.4", timeout=0.01) is None

    async def test_returns_none_on_generic_error(self):
        with patch(
            "src.services.server_query.a2s.info",
            side_effect=OSError("connection refused"),
        ):
            assert await query("1.2.3.4") is None

    async def test_forwards_address_and_timeout(self):
        with patch("src.services.server_query.a2s.info", return_value=_fake_a2s_info()) as info:
            await query("9.9.9.9", port=2457, timeout=1.5)
        info.assert_called_once_with(("9.9.9.9", 2457), timeout=1.5)
