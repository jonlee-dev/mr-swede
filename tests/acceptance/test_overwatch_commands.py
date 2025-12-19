"""Acceptance tests for Overwatch commands.

These tests implement the scenarios from overwatch.feature using pytest-bdd.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from src.cogs.overwatch import OverwatchCog
from src.database.models import Account, CompetitiveStats, RankInfo

# Load feature file scenarios
scenarios("features/overwatch.feature")


# ==================== Fixtures ====================

@pytest.fixture
def overwatch_cog(mock_bot, mock_firestore_client):
    """Create OverwatchCog with mocked dependencies."""
    with patch("src.cogs.overwatch.get_firestore_client", return_value=mock_firestore_client):
        cog = OverwatchCog(mock_bot)
        cog.db = mock_firestore_client
        cog.overfast = AsyncMock()
        return cog


@pytest.fixture
def context():
    """Shared context between steps."""
    return {"response_embed": None, "error": None}


# ==================== Given Steps ====================

@given("the bot is connected to Discord")
def bot_connected(mock_bot):
    """Ensure bot is connected."""
    mock_bot.is_ready.return_value = True


@given("the Overfast API is available")
def overfast_available(overwatch_cog):
    """Ensure Overfast API is mocked and available."""
    overwatch_cog.overfast.check_health = AsyncMock(return_value=True)


@given(parsers.parse('a player with BattleTag "{battletag}" exists'))
def player_exists(overwatch_cog, battletag):
    """Set up mock for existing player."""
    stats = CompetitiveStats(
        tank=RankInfo(division="Diamond", tier=3),
        damage=RankInfo(division="Master", tier=2),
        support=RankInfo(division="Platinum", tier=1),
    )
    overwatch_cog.overfast.get_competitive_stats = AsyncMock(return_value=stats)


@given("I am not tracking any accounts")
def no_tracked_accounts(overwatch_cog):
    """Set up empty account list."""
    overwatch_cog.db.get_accounts_by_discord_user = AsyncMock(return_value=[])
    overwatch_cog.db.get_account_by_battle_tag = AsyncMock(return_value=None)


@given(parsers.parse('I am tracking account "{battletag}"'))
def tracking_single_account(overwatch_cog, battletag):
    """Set up single tracked account."""
    account = Account(
        id="test-id",
        battle_tag=battletag,
        discord_user_id="111222333",
        display_name=battletag.split("#")[0],
        is_main=True,
        current_stats=CompetitiveStats(
            tank=RankInfo(division="Gold", tier=2),
        ),
    )
    overwatch_cog.db.get_accounts_by_discord_user = AsyncMock(return_value=[account])
    overwatch_cog.db.get_account_by_battle_tag = AsyncMock(return_value=account)


@given(parsers.parse('I am tracking accounts "{tag1}" and "{tag2}"'))
def tracking_multiple_accounts(overwatch_cog, tag1, tag2):
    """Set up multiple tracked accounts."""
    accounts = [
        Account(
            id=f"id-{i}",
            battle_tag=tag,
            discord_user_id="111222333",
            display_name=tag.split("#")[0],
            current_stats=CompetitiveStats(),
        )
        for i, tag in enumerate([tag1, tag2])
    ]
    overwatch_cog.db.get_accounts_by_discord_user = AsyncMock(return_value=accounts)


@given("I am tracking multiple accounts")
def tracking_accounts(overwatch_cog):
    """Set up multiple tracked accounts."""
    accounts = [
        Account(
            id=f"id-{i}",
            battle_tag=f"Player{i}#111{i}",
            discord_user_id="111222333",
            display_name=f"Player{i}",
            current_stats=CompetitiveStats(
                tank=RankInfo(division="Gold", tier=i+1),
            ),
        )
        for i in range(3)
    ]
    overwatch_cog.db.get_accounts_by_discord_user = AsyncMock(return_value=accounts)


@given("multiple users are tracking accounts")
def multiple_users_tracking(overwatch_cog):
    """Set up accounts from multiple users."""
    accounts = [
        Account(
            id=f"id-{i}",
            battle_tag=f"Player{i}#111{i}",
            discord_user_id=f"user{i}",
            display_name=f"Player{i}",
            current_stats=CompetitiveStats(
                tank=RankInfo(division=div, tier=1),
            ),
        )
        for i, div in enumerate(["Champion", "Grandmaster", "Master", "Diamond"])
    ]
    overwatch_cog.db.get_all_accounts = AsyncMock(return_value=accounts)


# ==================== When Steps ====================

@when(parsers.parse('I execute the command "/ow stats {battletag}"'))
async def execute_stats_command(overwatch_cog, mock_interaction, battletag, context):
    """Execute the stats command."""
    await overwatch_cog.stats.callback(overwatch_cog, mock_interaction, battletag)
    
    # Capture response
    if mock_interaction.followup.send.called:
        call_args = mock_interaction.followup.send.call_args
        context["response_embed"] = call_args.kwargs.get("embed")


@when(parsers.parse('I execute the command "/ow track {battletag}"'))
async def execute_track_command(overwatch_cog, mock_interaction, battletag, context):
    """Execute the track command."""
    await overwatch_cog.track.callback(overwatch_cog, mock_interaction, battletag)
    
    if mock_interaction.followup.send.called:
        call_args = mock_interaction.followup.send.call_args
        context["response_embed"] = call_args.kwargs.get("embed")


@when(parsers.parse('I execute the command "/ow list"'))
async def execute_list_command(overwatch_cog, mock_interaction, context):
    """Execute the list command."""
    await overwatch_cog.list_accounts.callback(overwatch_cog, mock_interaction)
    
    if mock_interaction.followup.send.called:
        call_args = mock_interaction.followup.send.call_args
        context["response_embed"] = call_args.kwargs.get("embed")


@when(parsers.parse('I execute the command "/ow untrack {battletag}"'))
async def execute_untrack_command(overwatch_cog, mock_interaction, battletag, context):
    """Execute the untrack command."""
    await overwatch_cog.untrack.callback(overwatch_cog, mock_interaction, battletag)
    
    if mock_interaction.followup.send.called:
        call_args = mock_interaction.followup.send.call_args
        context["response_embed"] = call_args.kwargs.get("embed")


@when(parsers.parse('I execute the command "/ow refresh"'))
async def execute_refresh_command(overwatch_cog, mock_interaction, context):
    """Execute the refresh command."""
    await overwatch_cog.refresh.callback(overwatch_cog, mock_interaction)
    
    if mock_interaction.followup.send.called:
        call_args = mock_interaction.followup.send.call_args
        context["response_embed"] = call_args.kwargs.get("embed")


@when(parsers.parse('I execute the command "/ow leaderboard"'))
async def execute_leaderboard_command(overwatch_cog, mock_interaction, context):
    """Execute the leaderboard command."""
    await overwatch_cog.leaderboard.callback(overwatch_cog, mock_interaction)
    
    if mock_interaction.followup.send.called:
        call_args = mock_interaction.followup.send.call_args
        context["response_embed"] = call_args.kwargs.get("embed")


# ==================== Then Steps ====================

@then("I should see an embed with the player's stats")
def verify_stats_embed(context):
    """Verify stats embed was sent."""
    embed = context["response_embed"]
    assert embed is not None
    assert "Stats" in embed.title or "📊" in embed.title


@then("the embed should show Tank, Damage, and Support ranks")
def verify_role_ranks(context):
    """Verify all role ranks are shown."""
    embed = context["response_embed"]
    field_names = [f.name for f in embed.fields]
    assert any("Tank" in name or "🛡️" in name for name in field_names)
    assert any("Damage" in name or "⚔️" in name for name in field_names)
    assert any("Support" in name or "💚" in name for name in field_names)


@then("the account should be saved to the database")
def verify_account_saved(overwatch_cog):
    """Verify account was created in database."""
    overwatch_cog.db.create_account.assert_called_once()


@then("I should see a confirmation message with current ranks")
def verify_track_confirmation(context):
    """Verify tracking confirmation embed."""
    embed = context["response_embed"]
    assert embed is not None
    assert "✅" in embed.title or "Tracked" in embed.title


@then("I should see both accounts listed")
def verify_accounts_listed(context):
    """Verify multiple accounts shown."""
    embed = context["response_embed"]
    assert embed is not None
    assert len(embed.fields) >= 2


@then("each account should show its current rank")
def verify_ranks_shown(context):
    """Verify ranks are shown for each account."""
    embed = context["response_embed"]
    for field in embed.fields:
        # Each field should have rank info
        assert "🛡️" in field.value or "Tank" in field.value or "Unranked" in field.value


@then("the account should be removed from tracking")
def verify_account_deleted(overwatch_cog):
    """Verify account was deleted."""
    overwatch_cog.db.delete_account.assert_called_once()


@then("I should see a confirmation message")
def verify_confirmation(context):
    """Verify confirmation message was sent."""
    embed = context["response_embed"]
    assert embed is not None


@then("all account stats should be updated from the API")
def verify_stats_updated(overwatch_cog):
    """Verify stats were fetched for all accounts."""
    assert overwatch_cog.overfast.get_competitive_stats.called


@then("stats history should be recorded")
def verify_history_recorded(overwatch_cog):
    """Verify stats history was saved."""
    overwatch_cog.db.add_stats_history.assert_called()


@then("I should see accounts ranked by highest rank")
def verify_leaderboard_ranking(context):
    """Verify leaderboard shows ranked accounts."""
    embed = context["response_embed"]
    assert embed is not None
    assert "Leaderboard" in embed.title or "🏆" in embed.title


@then("the top 3 should have medal emojis")
def verify_medal_emojis(context):
    """Verify medal emojis for top 3."""
    embed = context["response_embed"]
    description = embed.description or ""
    assert "🥇" in description or "🥈" in description or "🥉" in description


@then("I should see an error message")
def verify_error_shown(context):
    """Verify error embed was shown."""
    embed = context["response_embed"]
    assert embed is not None
    assert "❌" in embed.title or "Error" in embed.title


@then("the error should mention checking the BattleTag format")
def verify_battletag_error(context):
    """Verify error mentions BattleTag."""
    embed = context["response_embed"]
    assert "BattleTag" in embed.description or "battletag" in embed.description.lower()

