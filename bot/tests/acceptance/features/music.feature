# Music Feature
# Acceptance tests written in Gherkin for ATDD

Feature: Music Playback
    As a Discord user
    I want to play music in voice channels
    So that I can listen to music with my friends

    Background:
        Given the bot is connected to Discord
        And YouTube audio extraction is available

    Scenario: Play a song by search query
        Given I am in voice channel "General"
        When I execute the command "/play never gonna give you up"
        Then the bot should join my voice channel
        And the song should start playing
        And I should see a "Now Playing" embed

    Scenario: Play a YouTube URL
        Given I am in voice channel "Music"
        When I execute the command "/play https://youtube.com/watch?v=abc123"
        Then the bot should join my voice channel
        And the song should start playing

    Scenario: Add song to queue while playing
        Given the bot is playing a song
        When I execute the command "/play another song"
        Then the song should be added to the queue
        And I should see an "Added to Queue" embed with position

    Scenario: Pause and resume playback
        Given the bot is playing a song
        When I execute the command "/pause"
        Then playback should be paused
        When I execute the command "/resume"
        Then playback should resume

    Scenario: Skip current song
        Given the bot is playing a song
        And there is a song in the queue
        When I execute the command "/skip"
        Then the current song should stop
        And the next song in queue should start playing

    Scenario: View queue
        Given the bot is playing a song
        And there are 5 songs in the queue
        When I execute the command "/queue"
        Then I should see the current song
        And I should see the queue list
        And I should see loop mode and volume

    Scenario: Stop playback and clear queue
        Given the bot is playing a song
        And there are songs in the queue
        When I execute the command "/stop"
        Then playback should stop
        And the queue should be empty

    Scenario: Change volume
        Given the bot is playing a song
        When I execute the command "/volume 75"
        Then the volume should be set to 75%

    Scenario: Set loop mode
        Given the bot is playing a song
        When I execute the command "/loop single"
        Then loop mode should be set to "single"
        And the current song should repeat when finished

    Scenario: Shuffle queue
        Given there are 5 songs in the queue
        When I execute the command "/shuffle"
        Then the queue order should be randomized

    Scenario: Leave voice channel
        Given the bot is in a voice channel
        When I execute the command "/leave"
        Then the bot should disconnect from voice
        And the queue should be cleared

    Scenario: Auto-disconnect when alone
        Given the bot is in a voice channel
        When all users leave the voice channel
        Then after 60 seconds the bot should auto-disconnect

    Scenario: Must be in voice channel to play
        Given I am not in any voice channel
        When I execute the command "/play some song"
        Then I should see an error message
        And the error should say I must be in a voice channel

    Scenario: Play Spotify track
        Given I am in voice channel "Music"
        When I execute the command "/play https://open.spotify.com/track/abc123"
        Then the bot should search YouTube for the track
        And the song should start playing

