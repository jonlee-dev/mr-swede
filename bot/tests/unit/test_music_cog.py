"""Unit tests for src.cogs.music.

Per the PRD: services/music.py is intentionally NOT unit-tested (we
exercise it via the live Lavalink integration probe). The cog, by
contrast, IS unit-tested -- it has branchy logic for selecting which
embed to render based on the PlayResult shape, and that's exactly the
kind of thing tests catch.

We mock src.services.music wholesale so these tests don't need
wavelink, discord voice clients, or a live Lavalink. The cog only
sees the PlayResult dataclass; the test asserts which embed branch
fires for each shape.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from src.cogs.music import MusicCog, _playlist_embed, _track_embed
from src.config.settings import Settings
from src.services.music import PLAYLIST_TRACK_CAP, PlayResult, TrackInfo


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("GCP_PROJECT_ID", "test-proj")
    monkeypatch.setenv("LAVALINK_INSTANCE_NAME", "lavalink-server")
    monkeypatch.setenv("LAVALINK_ZONE", "us-central1-a")
    monkeypatch.setenv("LAVALINK_PORT", "2333")
    monkeypatch.setenv("LAVALINK_HOST", "1.2.3.4")  # skip GCE start dance
    monkeypatch.setenv("MUSIC_COMMAND_CHANNEL_ID", "555")
    return Settings()


@pytest.fixture
def cog(settings: Settings) -> MusicCog:
    bot = MagicMock()
    with patch("src.cogs.music.get_settings", return_value=settings):
        return MusicCog(bot)


def _interaction() -> MagicMock:
    """Build a discord.Interaction mock that supports defer + followup."""
    interaction = MagicMock(spec=discord.Interaction)
    # discord.Member with a current voice channel; the play handler
    # bails early if member.voice is None.
    member = MagicMock(spec=discord.Member)
    voice_state = MagicMock()
    voice_state.channel = MagicMock()
    member.voice = voice_state
    member.id = 11122233
    interaction.user = member
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _track_info(title: str = "Test Song", duration_ms: int = 180_000) -> TrackInfo:
    return TrackInfo(
        title=title,
        author="Test Artist",
        duration_ms=duration_ms,
        uri="https://example.com/track",
        requester_id=11122233,
    )


def _result_single(queue_position: int = 0) -> PlayResult:
    return PlayResult(
        first_track=_track_info(),
        first_track_queue_position=queue_position,
        playlist_title=None,
        extra_tracks_queued=0,
        truncated_from=None,
        unresolved_count=0,
    )


def _result_playlist(
    extra: int = 9,
    title: str = "Friday Night Hype",
    truncated_from: int | None = None,
    unresolved: int = 0,
) -> PlayResult:
    return PlayResult(
        first_track=_track_info(),
        first_track_queue_position=0,
        playlist_title=title,
        extra_tracks_queued=extra,
        truncated_from=truncated_from,
        unresolved_count=unresolved,
    )


def _result_no_results() -> PlayResult:
    return PlayResult(
        first_track=None,
        first_track_queue_position=0,
        playlist_title=None,
        extra_tracks_queued=0,
        truncated_from=None,
        unresolved_count=0,
    )


class TestTrackEmbed:
    """Pure embed-rendering tests -- no async, no mocks."""

    def test_now_playing_renders_title_artist_duration(self):
        embed = _track_embed(_track_info(title="Sandstorm", duration_ms=233_000), "Now playing")
        assert embed.title == "Now playing"
        assert "Sandstorm" in (embed.description or "")
        field_text = " ".join(f"{f.name}={f.value}" for f in embed.fields)
        assert "Test Artist" in field_text
        assert "3:53" in field_text  # 233s -> 3:53

    def test_includes_requester_when_set(self):
        embed = _track_embed(_track_info(), "Now playing")
        assert any("11122233" in str(f.value) for f in embed.fields)


class TestPlaylistEmbed:
    """The new v4.2 surface -- this is where the branching lives."""

    def test_basic_playlist_summary(self):
        embed = _playlist_embed(_result_playlist(extra=9, title="Friday Night Hype"))
        assert "10 tracks" in (embed.title or "")  # 1 first + 9 extra
        assert "Friday Night Hype" in (embed.description or "")
        # First-up field is always present.
        assert any(f.name == "First up" for f in embed.fields)

    def test_truncation_field_appears_only_when_truncated(self):
        no_trunc = _playlist_embed(_result_playlist(extra=9, truncated_from=None))
        with_trunc = _playlist_embed(_result_playlist(extra=99, truncated_from=523))

        assert not any(f.name == "Truncated" for f in no_trunc.fields)
        trunc_text = next(f.value for f in with_trunc.fields if f.name == "Truncated")
        assert "523" in trunc_text
        assert str(PLAYLIST_TRACK_CAP) in trunc_text

    def test_unresolved_field_appears_only_when_some_unresolved(self):
        no_unres = _playlist_embed(_result_playlist(extra=9, unresolved=0))
        with_unres = _playlist_embed(_result_playlist(extra=27, unresolved=3))

        assert not any(f.name == "Unresolved" for f in no_unres.fields)
        unres_text = next(f.value for f in with_unres.fields if f.name == "Unresolved")
        assert "3" in unres_text

    def test_missing_playlist_title_falls_back_to_generic(self):
        embed = _playlist_embed(_result_playlist(title=""))  # falsy
        # Cog falls back to "playlist" generic name.
        assert "playlist" in (embed.description or "").lower()


class TestPlayCommand:
    """Branch coverage for /music play -- single, playlist, no-results."""

    async def test_no_voice_channel_short_circuits(self, cog: MusicCog):
        interaction = _interaction()
        interaction.user.voice = None  # user not in a VC

        await MusicCog.play.callback(cog, interaction, query="anything")

        # Followup says "join VC first", services.music.play is never called.
        interaction.followup.send.assert_awaited_once()
        sent_text = interaction.followup.send.call_args.args[0]
        assert "voice channel" in sent_text.lower()

    async def test_single_track_renders_track_embed(self, cog: MusicCog):
        interaction = _interaction()
        with (
            patch.object(cog, "_ensure_lavalink_running", AsyncMock(return_value="1.2.3.4")),
            patch.object(cog, "_ensure_node_connected", AsyncMock(return_value=True)),
            patch(
                "src.cogs.music.music.play",
                AsyncMock(return_value=_result_single(queue_position=0)),
            ),
        ):
            await MusicCog.play.callback(cog, interaction, query="hello")

        # One followup call, with an embed. Make sure we got the
        # single-track flavor (title is "Now playing" or "Queued (#N)").
        kwargs = interaction.followup.send.call_args.kwargs
        embed = kwargs["embed"]
        assert embed.title in {"Now playing"} or "Queued" in (embed.title or "")

    async def test_queued_track_uses_queue_position_in_header(self, cog: MusicCog):
        interaction = _interaction()
        with (
            patch.object(cog, "_ensure_lavalink_running", AsyncMock(return_value="1.2.3.4")),
            patch.object(cog, "_ensure_node_connected", AsyncMock(return_value=True)),
            patch(
                "src.cogs.music.music.play",
                AsyncMock(return_value=_result_single(queue_position=4)),
            ),
        ):
            await MusicCog.play.callback(cog, interaction, query="hello")

        embed = interaction.followup.send.call_args.kwargs["embed"]
        assert embed.title == "Queued (#4)"

    async def test_playlist_renders_playlist_embed(self, cog: MusicCog):
        interaction = _interaction()
        with (
            patch.object(cog, "_ensure_lavalink_running", AsyncMock(return_value="1.2.3.4")),
            patch.object(cog, "_ensure_node_connected", AsyncMock(return_value=True)),
            patch(
                "src.cogs.music.music.play",
                AsyncMock(return_value=_result_playlist(extra=29)),
            ),
        ):
            await MusicCog.play.callback(cog, interaction, query="https://spotify/playlist/x")

        embed = interaction.followup.send.call_args.kwargs["embed"]
        # Playlist embed says "Queued N tracks" in the title.
        assert "30 tracks" in (embed.title or "")

    async def test_truncated_playlist_surfaces_warning(self, cog: MusicCog):
        interaction = _interaction()
        with (
            patch.object(cog, "_ensure_lavalink_running", AsyncMock(return_value="1.2.3.4")),
            patch.object(cog, "_ensure_node_connected", AsyncMock(return_value=True)),
            patch(
                "src.cogs.music.music.play",
                AsyncMock(return_value=_result_playlist(extra=99, truncated_from=523)),
            ),
        ):
            await MusicCog.play.callback(cog, interaction, query="https://youtube/huge")

        embed = interaction.followup.send.call_args.kwargs["embed"]
        assert any(f.name == "Truncated" for f in embed.fields)

    async def test_unresolved_count_surfaces(self, cog: MusicCog):
        interaction = _interaction()
        with (
            patch.object(cog, "_ensure_lavalink_running", AsyncMock(return_value="1.2.3.4")),
            patch.object(cog, "_ensure_node_connected", AsyncMock(return_value=True)),
            patch(
                "src.cogs.music.music.play",
                AsyncMock(return_value=_result_playlist(extra=26, unresolved=3)),
            ),
        ):
            await MusicCog.play.callback(cog, interaction, query="https://spotify/playlist/y")

        embed = interaction.followup.send.call_args.kwargs["embed"]
        assert any(f.name == "Unresolved" for f in embed.fields)

    async def test_no_results_sends_ephemeral_message(self, cog: MusicCog):
        interaction = _interaction()
        with (
            patch.object(cog, "_ensure_lavalink_running", AsyncMock(return_value="1.2.3.4")),
            patch.object(cog, "_ensure_node_connected", AsyncMock(return_value=True)),
            patch(
                "src.cogs.music.music.play",
                AsyncMock(return_value=_result_no_results()),
            ),
        ):
            await MusicCog.play.callback(cog, interaction, query="asdfqwerty")

        # Ephemeral text reply, no embed.
        send_call = interaction.followup.send.call_args
        assert send_call.args, "expected a positional text arg for the no-results message"
        assert "no results" in send_call.args[0].lower()
        assert send_call.kwargs.get("ephemeral") is True

    async def test_play_failure_is_surfaced_ephemerally(self, cog: MusicCog):
        interaction = _interaction()
        with (
            patch.object(cog, "_ensure_lavalink_running", AsyncMock(return_value="1.2.3.4")),
            patch.object(cog, "_ensure_node_connected", AsyncMock(return_value=True)),
            patch(
                "src.cogs.music.music.play",
                AsyncMock(side_effect=RuntimeError("lavasrc unreachable")),
            ),
        ):
            await MusicCog.play.callback(cog, interaction, query="https://spotify/track/z")

        send_call = interaction.followup.send.call_args
        assert "couldn't play" in send_call.args[0].lower()
        assert send_call.kwargs.get("ephemeral") is True
