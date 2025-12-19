# Overwatch Stats Feature
# Acceptance tests written in Gherkin for ATDD

Feature: Overwatch Stats Tracking
    As a Discord user
    I want to track Overwatch player statistics
    So that I can monitor my rank progress and compare with friends

    Background:
        Given the bot is connected to Discord
        And the Overfast API is available

    Scenario: View player stats
        Given a player with BattleTag "TestPlayer#1234" exists
        When I execute the command "/ow stats TestPlayer#1234"
        Then I should see an embed with the player's stats
        And the embed should show Tank, Damage, and Support ranks

    Scenario: Track a new account
        Given I am not tracking any accounts
        When I execute the command "/ow track TestPlayer#1234"
        Then the account should be saved to the database
        And I should see a confirmation message with current ranks

    Scenario: Track account as main
        Given I am tracking account "Alt#5678"
        When I execute the command "/ow track Main#1234 main:True"
        Then "Main#1234" should be marked as my main account
        And "Alt#5678" should no longer be marked as main

    Scenario: List tracked accounts
        Given I am tracking accounts "Player1#1111" and "Player2#2222"
        When I execute the command "/ow list"
        Then I should see both accounts listed
        And each account should show its current rank

    Scenario: Untrack an account
        Given I am tracking account "OldAccount#9999"
        When I execute the command "/ow untrack OldAccount#9999"
        Then the account should be removed from tracking
        And I should see a confirmation message

    Scenario: Refresh all stats
        Given I am tracking multiple accounts
        When I execute the command "/ow refresh"
        Then all account stats should be updated from the API
        And stats history should be recorded

    Scenario: View leaderboard
        Given multiple users are tracking accounts
        When I execute the command "/ow leaderboard"
        Then I should see accounts ranked by highest rank
        And the top 3 should have medal emojis

    Scenario: Handle invalid BattleTag
        When I execute the command "/ow stats InvalidPlayer"
        Then I should see an error message
        And the error should mention checking the BattleTag format

