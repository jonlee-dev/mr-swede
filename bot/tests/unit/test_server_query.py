"""Unit tests for src.services.server_query.

Rewritten when the server_query implementation switched from python-a2s
(broken under Valheim crossplay) to HTTP fetch from the VM's
log-scraping daemon. Tests now mock httpx at the AsyncClient.get
boundary.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.server_query import LiveStatus, fetch_status


def _fake_response(payload: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock(return_value=None)
    resp.status_code = status_code
    return resp


class TestFetchStatus:
    async def test_returns_live_status_on_success(self):
        payload = {
            "last_update": "2026-04-28T05:25:06.512900+00:00",
            "join_code": "184520",
            "player_count": 3,
            "server_running": True,
        }
        client_mock = MagicMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=None)
        client_mock.get = AsyncMock(return_value=_fake_response(payload))

        with patch("src.services.server_query.httpx.AsyncClient", return_value=client_mock):
            status = await fetch_status("1.2.3.4")

        assert status == LiveStatus(
            join_code="184520",
            player_count=3,
            server_running=True,
            last_update="2026-04-28T05:25:06.512900+00:00",
        )

    async def test_returns_none_on_request_error(self):
        client_mock = MagicMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=None)
        client_mock.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("src.services.server_query.httpx.AsyncClient", return_value=client_mock):
            assert await fetch_status("1.2.3.4") is None

    async def test_returns_none_on_http_error(self):
        client_mock = MagicMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=None)
        bad_response = _fake_response({}, status_code=500)
        bad_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=bad_response)
        )
        client_mock.get = AsyncMock(return_value=bad_response)

        with patch("src.services.server_query.httpx.AsyncClient", return_value=client_mock):
            assert await fetch_status("1.2.3.4") is None

    async def test_passes_through_daemon_error_field(self):
        # When the daemon's own scrape failed, the JSON includes an
        # `error` field. We log + still return whatever fields are
        # present (server_running may still be authoritative).
        payload = {
            "last_update": "2026-04-28T05:25:06.512900+00:00",
            "join_code": None,
            "player_count": 0,
            "server_running": False,
            "error": "TimeoutError('timed out')",
        }
        client_mock = MagicMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=None)
        client_mock.get = AsyncMock(return_value=_fake_response(payload))

        with patch("src.services.server_query.httpx.AsyncClient", return_value=client_mock):
            status = await fetch_status("1.2.3.4")

        assert status is not None
        assert status.server_running is False
        assert status.join_code is None

    @pytest.mark.parametrize("port", [9001, 9002])
    async def test_constructs_url_with_port(self, port: int):
        client_mock = MagicMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=None)
        client_mock.get = AsyncMock(
            return_value=_fake_response(
                {
                    "last_update": "x",
                    "join_code": None,
                    "player_count": 0,
                    "server_running": False,
                }
            )
        )

        with patch("src.services.server_query.httpx.AsyncClient", return_value=client_mock):
            await fetch_status("9.9.9.9", port=port)

        client_mock.get.assert_called_once_with(f"http://9.9.9.9:{port}/status.json")
