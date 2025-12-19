# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2024-12-18

### 🎉 Complete Rewrite for Cloud Run

This release is a complete rewrite of Mr. Swede, transforming it from a local-only Discord bot into a cloud-native application designed for Google Cloud Run deployment.

### Added

#### Discord Bot
- **Modern slash commands** — Migrated from prefix commands (`$`) to Discord's slash command system
- **Cogs architecture** — Organized commands into modular cogs (General, Overwatch, Music)
- **Rich embeds** — Beautiful embedded messages for all command responses
- **Auto-disconnect** — Bot automatically leaves voice channels after 60 seconds of inactivity

#### Overwatch Features
- **Multi-account tracking** — Track your main account and unlimited alts
- **Firestore persistence** — Account data and stats history stored in cloud database
- **Stats history** — Track your rank changes over time
- **Server leaderboard** — `/ow leaderboard` shows ranking across all tracked accounts
- **Overfast API integration** — Reliable stats fetching via community API

#### Music Features
- **YouTube playback** — Play from URLs or search queries
- **Spotify URL support** — Paste Spotify links, bot searches YouTube for playback
- **Queue management** — Full queue with add, skip, shuffle, loop modes
- **Volume control** — Adjustable playback volume
- **Now playing** — Rich embed showing current track info

#### Infrastructure
- **Cloud Run deployment** — Serverless container deployment with auto-scaling
- **Google Secret Manager** — Secure credential management (no more `.env` in production)
- **Firestore database** — NoSQL database for account and stats persistence
- **Health check endpoint** — `/health` endpoint for Cloud Run lifecycle management
- **Cloud Build CI/CD** — Automatic build and deploy on git push

#### Developer Experience
- **Poetry** — Modern Python dependency management replacing pip/requirements.txt
- **Pydantic Settings** — Type-safe configuration with validation
- **Structured logging** — JSON logs with structlog for Cloud Logging integration
- **ATDD testing** — Acceptance tests with pytest-bdd and Gherkin feature files
- **Pre-commit hooks** — Automatic code quality checks
- **GitHub Actions CI** — Linting, type checking, and tests on every PR

### Changed

- **Python version** — Upgraded from 3.8 to 3.11
- **discord.py version** — Upgraded from 1.7 to 2.3
- **Audio backend** — Replaced youtube-dl with yt-dlp (actively maintained fork)
- **Configuration** — Replaced YAML config with Pydantic settings
- **Logging** — Replaced Python logging with structlog
- **Data storage** — Replaced Excel file with Firestore

### Removed

- **Prefix commands** — All `$` commands removed in favor of slash commands
- **Excel storage** — `accounts.xlsx` replaced with Firestore
- **Twitch integration** — Removed unused Twitch API code
- **VLC player** — Removed VLC dependency, using FFmpeg directly
- **Google Sheets integration** — Replaced with Firestore

### Migration Guide

If you were using the previous version:

1. **Export your accounts** — Your `accounts.xlsx` data needs to be migrated manually to Firestore
2. **Update secrets** — Move credentials from `.env` to Google Secret Manager
3. **Update bot permissions** — Re-invite the bot with new OAuth2 URL (needs `applications.commands` scope)
4. **Learn new commands** — All commands now use `/` prefix (e.g., `/ow stats Player#1234`)

### Technical Details

#### New Project Structure
```
mr-swede/
├── src/                    # Application code
│   ├── main.py             # Entry point
│   ├── bot.py              # Discord bot setup
│   ├── config/             # Configuration
│   ├── cogs/               # Command modules
│   ├── services/           # API clients
│   ├── database/           # Firestore
│   └── utils/              # Helpers
├── tests/                  # Test suite
├── Dockerfile              # Container build
├── cloudbuild.yaml         # GCP CI/CD
└── pyproject.toml          # Dependencies
```

#### API Clients
- `OverfastClient` — Overwatch stats via Overfast API
- `BlizzardClient` — Blizzard OAuth + Hearthstone API
- `SpotifyClient` — Track metadata and playlist info
- `YouTubeAudioClient` — Audio extraction via yt-dlp

#### Database Models
- `Account` — Overwatch account with BattleTag and stats
- `CompetitiveStats` — Rank info for all roles
- `StatsHistory` — Historical stats snapshots
- `UserPreferences` — User settings

---

## [1.0.0] - 2021-05-XX (Legacy)

### Original Release

Initial version of Mr. Swede with basic functionality:
- Discord bot with prefix commands (`$`)
- Overwatch stats lookup via web scraping
- Basic music playback via youtube-dl
- Local Excel file for account storage
- Local `.env` file for configuration

---

## Notes

### Versioning

- **Major versions (X.0.0)** — Breaking changes, architecture rewrites
- **Minor versions (0.X.0)** — New features, backward compatible
- **Patch versions (0.0.X)** — Bug fixes, minor improvements

### Links

- [GitHub Repository](https://github.com/jonlee-dev/mr-swede)
- [Overfast API](https://overfast-api.tekrop.fr/)
- [Discord.py Documentation](https://discordpy.readthedocs.io/)

