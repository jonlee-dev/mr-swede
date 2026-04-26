"""Unit tests for src.cogs.valheim."""

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from src.cogs.valheim import ValheimCog, build_status_embed
from src.config.settings import Settings
from src.services.compute import InstanceState
from src.services.server_query import GameState


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    # Settings fields use env-style aliases (GCP_PROJECT_ID etc.) so we set
    # them through the env to match how production configures the bot.
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


def _game(player_count: int = 2) -> GameState:
    return GameState(
        server_name="Mr. Swede",
        map_name="Midgard",
        player_count=player_count,
        max_players=10,
    )


class TestBuildStatusEmbed:
    """The embed builder is pure — exercise the rendering branches directly."""

    def test_running_with_game(self):
        embed = build_status_embed(_state(status="RUNNING"), _game(player_count=3))
        text = (embed.title or "") + " ".join(f.value for f in embed.fields)
        assert "RUNNING" in text
        assert "1.2.3.4" in text
        assert "3/10" in text
        assert "Midgard" in text

    def test_running_but_game_unreachable(self):
        embed = build_status_embed(_state(status="RUNNING"), None)
        text = (embed.title or "") + " ".join(f.value for f in embed.fields)
        assert "RUNNING" in text
        assert "1.2.3.4" in text
        # No A2S → no player count rendered
        assert "/10" not in text

    def test_terminated_hides_ip_and_game_fields(self):
        embed = build_status_embed(_state(status="TERMINATED", public_ip=None), None)
        text = (embed.title or "") + " ".join(f.value for f in embed.fields)
        assert "TERMINATED" in text
        assert "1.2.3.4" not in text
        assert "/10" not in text

    def test_transition_state(self):
        embed = build_status_embed(_state(status="STAGING", public_ip=None), None)
        text = (embed.title or "") + " ".join(f.value for f in embed.fields)
        assert "STAGING" in text


class TestStatusCommand:
    async def test_running_queries_a2s_and_sends_embed(self, cog: ValheimCog):
        interaction = _interaction()
        with (
            patch(
                "src.cogs.valheim.compute.describe_instance",
                AsyncMock(return_value=_state(status="RUNNING")),
            ) as describe,
            patch(
                "src.cogs.valheim.server_query.query",
                AsyncMock(return_value=_game(player_count=4)),
            ) as query,
        ):
            await ValheimCog.status.callback(cog, interaction)

        describe.assert_awaited_once_with("test-proj", "us-central1-a", "valheim-server")
        query.assert_awaited_once_with("1.2.3.4")
        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        kwargs = interaction.followup.send.call_args.kwargs
        assert isinstance(kwargs["embed"], discord.Embed)
        assert kwargs.get("ephemeral", False) is False

    async def test_terminated_skips_a2s(self, cog: ValheimCog):
        interaction = _interaction()
        with (
            patch(
                "src.cogs.valheim.compute.describe_instance",
                AsyncMock(return_value=_state(status="TERMINATED", public_ip=None)),
            ),
            patch("src.cogs.valheim.server_query.query", AsyncMock()) as query,
        ):
            await ValheimCog.status.callback(cog, interaction)

        query.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()


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
        kwargs = interaction.followup.send.call_args.kwargs
        msg = kwargs.get("content", "") or ""
        assert "Starting" in msg or "starting" in msg
        assert kwargs.get("ephemeral", False) is False

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
        kwargs = interaction.followup.send.call_args.kwargs
        msg = kwargs.get("content", "") or ""
        assert "1.2.3.4" in msg
        assert "running" in msg.lower() or "up" in msg.lower()


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
        kwargs = interaction.followup.send.call_args.kwargs
        msg = kwargs.get("content", "") or ""
        assert "Stopping" in msg or "stopping" in msg
        assert kwargs.get("ephemeral", False) is False

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
        kwargs = interaction.followup.send.call_args.kwargs
        msg = kwargs.get("content", "") or ""
        assert "stopped" in msg.lower() or "already" in msg.lower()
