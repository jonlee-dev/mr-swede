"""Unit tests for src.cogs.valheim.

Updated when server_query.query (A2S) became fetch_status (HTTP) and
the embed builder gained the password parameter.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from src.cogs.valheim import ValheimCog, build_status_embed
from src.config.settings import Settings
from src.services.compute import InstanceState
from src.services.server_query import LiveStatus


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("GCP_PROJECT_ID", "test-proj")
    monkeypatch.setenv("VALHEIM_ZONE", "us-central1-a")
    monkeypatch.setenv("VALHEIM_INSTANCE_NAME", "valheim-server")
    return Settings()


@pytest.fixture
def cog(settings: Settings) -> ValheimCog:
    bot = MagicMock()
    with patch("src.cogs.valheim.get_settings", return_value=settings):
        return ValheimCog(bot)


def _interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _state(status: str = "RUNNING", public_ip: str | None = "1.2.3.4") -> InstanceState:
    return InstanceState(
        name="valheim-server",
        zone="us-central1-a",
        status=status,
        public_ip=public_ip,
        machine_type="e2-standard-2",
    )


def _live(player_count: int = 2, join_code: str | None = "184520") -> LiveStatus:
    return LiveStatus(
        join_code=join_code,
        player_count=player_count,
        server_running=True,
        last_update="2026-04-28T05:25:06.512900+00:00",
    )


class TestBuildStatusEmbed:
    """The embed builder is pure -- exercise the rendering branches directly."""

    def test_running_with_live_data(self):
        embed = build_status_embed(
            _state(status="RUNNING"),
            _live(player_count=3),
            password="hunter2",
        )
        text = (embed.title or "") + " ".join(f.value for f in embed.fields)
        assert "RUNNING" in text
        assert "1.2.3.4" in text
        assert "184520" in text  # join code
        assert "3" in text  # player count
        assert "hunter2" in text  # password

    def test_running_steam_only_no_join_code(self):
        # CROSSPLAY=false path: server is up but the daemon's
        # join_code is null because the server doesn't register with
        # PlayFab. Embed should fall back to a "How to join" field
        # that walks the user to Valheim's Join IP menu.
        embed = build_status_embed(
            _state(status="RUNNING"),
            _live(player_count=2, join_code=None),
            password="hunter2",
        )
        text = (embed.title or "") + " ".join(f.value for f in embed.fields)
        names = [f.name for f in embed.fields]
        assert "RUNNING" in text
        assert "1.2.3.4" in text
        assert "Join code" not in names
        assert "How to join" in names
        # The hint should reference the Join IP path explicitly so
        # first-time joiners aren't searching the UI.
        join_hint = next(f.value for f in embed.fields if f.name == "How to join")
        assert "Join IP" in join_hint

    def test_running_but_status_unreachable(self):
        embed = build_status_embed(_state(status="RUNNING"), live=None, password=None)
        text = (embed.title or "") + " ".join(f.value for f in embed.fields)
        assert "RUNNING" in text
        assert "1.2.3.4" in text
        # No live data -> embed mentions "isn't answering yet"
        assert "answering" in text or "Try" in text

    def test_terminated_hides_runtime_fields(self):
        embed = build_status_embed(
            _state(status="TERMINATED", public_ip=None), live=None, password=None
        )
        text = (embed.title or "") + " ".join(f.value for f in embed.fields)
        assert "TERMINATED" in text
        # No IP, no join code, no password rendered when stopped.
        assert "1.2.3.4" not in text


class TestStartCommand:
    async def test_issues_start_when_terminated(self, cog: ValheimCog):
        interaction = _interaction()
        with (
            patch(
                "src.cogs.valheim.compute.describe_instance",
                AsyncMock(return_value=_state(status="TERMINATED", public_ip=None)),
            ),
            patch(
                "src.cogs.valheim.compute.start_instance",
                AsyncMock(return_value=True),
            ) as start,
        ):
            await ValheimCog.start.callback(cog, interaction)

        start.assert_awaited_once_with("test-proj", "us-central1-a", "valheim-server")

    async def test_already_running_does_not_call_start(self, cog: ValheimCog):
        interaction = _interaction()
        with (
            patch(
                "src.cogs.valheim.compute.describe_instance",
                AsyncMock(return_value=_state(status="RUNNING")),
            ),
            patch("src.cogs.valheim.compute.start_instance", AsyncMock()) as start,
        ):
            await ValheimCog.start.callback(cog, interaction)

        start.assert_not_awaited()


class TestStopCommand:
    async def test_issues_stop_when_running(self, cog: ValheimCog):
        interaction = _interaction()
        with (
            patch(
                "src.cogs.valheim.compute.describe_instance",
                AsyncMock(return_value=_state(status="RUNNING")),
            ),
            patch(
                "src.cogs.valheim.compute.stop_instance",
                AsyncMock(return_value=True),
            ) as stop,
        ):
            await ValheimCog.stop.callback(cog, interaction)

        stop.assert_awaited_once_with("test-proj", "us-central1-a", "valheim-server")

    async def test_already_terminated_does_not_call_stop(self, cog: ValheimCog):
        interaction = _interaction()
        with (
            patch(
                "src.cogs.valheim.compute.describe_instance",
                AsyncMock(return_value=_state(status="TERMINATED", public_ip=None)),
            ),
            patch("src.cogs.valheim.compute.stop_instance", AsyncMock()) as stop,
        ):
            await ValheimCog.stop.callback(cog, interaction)

        stop.assert_not_awaited()
