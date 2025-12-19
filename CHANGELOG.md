# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.2] - 2024-12-19

### 🧵 Improved Async & Non-Blocking Threading

This release improves bot responsiveness during music playback by better isolating blocking operations.

### Changed

- **Dedicated FFmpeg executor** — FFmpegPCMAudio now runs in its own thread pool (2 workers) instead of sharing the default executor
- **Increased yt-dlp workers** — Thread pool increased from 3 to 4 workers for better concurrency
- **Parallel playlist loading** — Spotify playlists now fetch tracks concurrently (3 at a time via semaphore) instead of sequentially
- **Thread-safe callbacks** — `after_playing` callback now uses `call_soon_threadsafe` for lower latency scheduling
- **FFmpeg timeout** — Added 30-second timeout for FFmpeg initialization to prevent hangs

### Fixed

- **Bot unresponsive during music processing** — FFmpeg and yt-dlp operations fully isolated in dedicated thread pools
- **Sequential playlist loading slow** — Playlist tracks now load ~3x faster with concurrent fetching
- **Callback scheduling issues** — Improved thread safety in playback completion callback

### Added

- **`get_ffmpeg_executor()` function** — Access the FFmpeg thread pool for custom audio processing
- **`get_ytdl_executor()` function** — Access the yt-dlp thread pool for custom extraction

---

## [2.1.1] - 2024-12-19

### 🎵 Music Playback Performance Fix

This release fixes the "websocket is Xs behind" error that caused Discord connection lag during music playback.

### Fixed

- **Websocket lag during music playback** — `yt-dlp` operations now run in a dedicated thread pool with timeouts, preventing event loop blocking
- **YouTube cookies blocking startup** — Cookies are now pre-loaded asynchronously during bot startup instead of on first play command
- **Deprecated asyncio patterns** — Replaced `asyncio.get_event_loop().run_in_executor()` with `asyncio.get_running_loop()` for Python 3.10+ compatibility

### Changed

- **yt-dlp execution model** — All YouTube operations now use a dedicated `ThreadPoolExecutor` with 2 workers
- **Timeouts** — Added 60-second timeout for audio extraction (increased from 30s), 120-second timeout for playlist extraction
- **Timeout error handling** — `/play` now shows a helpful message when extraction times out instead of generic error
- ~~**SoundCloud support**~~ — Removed (SoundCloud now requires OAuth API credentials)
- **Audio URL caching** — Extracted audio URLs cached for 4 hours to avoid re-extraction
- **Track pre-fetching** — Next track in queue is pre-fetched while current track plays
- **Optimized yt-dlp settings** — Prefer opus/vorbis codecs, skip DASH/HLS manifests, reduced socket timeout
- **Faster FFmpeg startup** — Added `-analyzeduration 0 -probesize 32768` for instant playback start
- **Reduced FFmpeg latency** — Added `-nostdin -loglevel error -threads 2 -af aresample=async=1`

### Fixed

- **Bot crashing on playback errors** — Added comprehensive error handling in `_play_track` and `_play_next` to prevent crashes
- **Voice client disconnection crashes** — Now gracefully handles disconnected voice clients
- **Bot shutting down on exceptions** — Added global asyncio exception handler to catch unhandled errors
- **Auto-reconnect on disconnect** — Bot now automatically reconnects if connection is lost (30s retry delay)
- **Multi-source search crashes** — All search operations now wrapped in try/except to prevent crashes
- **Firestore 404 error** — Added separate `FIRESTORE_PROJECT` setting (defaults to `mr-swede`) for database connection
- **Project ID vs Name** — Secrets use project number (`749144818572`), Firestore uses project name (`mr-swede`)
- **FFmpeg blocking event loop** — Moved FFmpegPCMAudio creation to executor to prevent websocket lag
- **Socket timeout** — Added 15-second socket timeout to yt-dlp options to prevent hanging
- **Lazy initialization** — `YouTubeAudioClient` now lazily initializes options to avoid blocking on import

### Added

- **`/refresh-cookies` command** — Owner-only admin command to refresh YouTube cookies from GSM without restarting the bot
- **`preload_cookies()` function** — Async function to fetch YouTube cookies from GSM during bot startup
- **Cookies caching** — Cookies fetched once and cached to `/tmp/youtube_cookies.txt`
- **Overwatch stats caching** — In-memory cache (5 min TTL) to reduce Overfast API calls and avoid rate limits
- **Better error messages** — `/ow stats` now shows specific errors for rate limiting (429), player not found (404), and other API errors
- **Global app command error handler** — Gracefully handles "Unknown interaction" (10062) errors when Discord interactions expire

### Changed (API Rate Limiting)

- **Disabled all API retries** — APIs have strict rate limits, so all retry logic has been removed:
  - `yt-dlp`: Set `retries=0`, `fragment_retries=0`, `extractor_retries=0`, `file_access_retries=0`
  - `spotipy`: Set `retries=0`, `status_retries=0`
  - `httpx` (Blizzard, Overfast): Added explicit `AsyncHTTPTransport(retries=0)`

### Technical Details

The "Can't keep up, websocket is Xs behind" error occurs when synchronous operations block Python's async event loop. Discord.py's heartbeat mechanism expects timely responses, and blocking operations cause the connection to fall behind.

**Root causes fixed:**
1. `yt-dlp.extract_info()` — CPU-intensive operation moved to dedicated thread pool
2. `SecretManagerServiceClient.access_secret_version()` — Network I/O moved to thread pool
3. No timeout handling — Operations could hang indefinitely

---

## [2.1.0] - 2024-12-18

### 🔐 Secrets Management & Testing Improvements

This release introduces a robust secrets management system using Google Secret Manager with JSON-structured secrets, along with comprehensive test coverage.

### Added

#### Secrets Management
- **SecretManager class** — Centralized secrets handling with GSM integration
- **JSON secrets support** — Parse structured JSON secrets from GSM
- **Dot-notation access** — Access nested keys (e.g., `mr-swede.token`) from JSON secrets
- **Environment variable fallback** — Local development uses env vars when GSM unavailable
- **Secrets caching** — Fetched secrets cached to minimize API calls
- **Typed dataclasses** — `BlizzardSecrets`, `DiscordBotSecrets`, `SpotifySecrets`, `AppSecrets`

#### Testing
- **SecretManager unit tests** — Full coverage for secrets loading, parsing, and caching
- **Updated service tests** — Tests reflect new secrets-based credential injection
- **Acceptance test improvements** — Simplified Overwatch command scenarios

### Changed

- **Python version** — Upgraded from 3.11 to 3.12
- **Bot token retrieval** — Now uses `get_bot_token()` from secrets module
- **Service initialization** — Blizzard and Spotify clients load credentials from `AppSecrets`
- **Dockerfile** — Updated to Python 3.12 base image

### Fixed

- **datetime.utcnow() deprecation** — Replaced all `datetime.utcnow()` calls with timezone-aware `datetime.now(UTC)` for Python 3.12+ compatibility
- **event_loop fixture deprecation** — Removed custom pytest event_loop fixture, using pytest-asyncio's built-in handling
- **Test warnings** — Suppressed library warnings from discord.py (audioop) and pytest-bdd (usefixtures) in pyproject.toml

### Documentation

- **User interaction guide** — New README section explaining how to use slash commands
- **Environment variables** — Reorganized into logical groups (Core, Discord, GCP, Local Dev)
- **GSM secrets structure** — Added JSON examples and secret structure diagrams
- **Architecture diagram** — Added `secrets.py` and `env.example` to file tree

### Infrastructure

- **Removed `cloudbuild.yaml`** — Cloud Run deploys directly from GitHub, no build config needed
- **Cost-optimized Cloud Run settings** — CPU throttling reduces costs from ~$35/month to ~$3-5/month
- **Resource tuning** — 512Mi memory, 1 vCPU, min/max instances = 1
- **Added CLI commands** — TODO.md includes `gcloud run services update` for applying settings

### Technical Details

#### Secrets Architecture
```
GSM Secrets (JSON format):
├── blizzard-secrets      → client_id, client_secret
├── discord-bot-secrets   → mr-swede.token, ow2-ranked-bot.token, etc.
└── spotify-secrets       → client_id, client_secret

Code usage:
  secrets = get_secrets()
  token = secrets.discord.mr_swede_token
  client_id = secrets.blizzard.client_id
```

#### Environment Variable Fallback
For local development without GSM access:
```bash
export DISCORD_BOT_TOKEN="your-token"
export BLIZZARD_CLIENT_ID="your-id"
export BLIZZARD_CLIENT_SECRET="your-secret"
export SPOTIFY_CLIENT_ID="your-id"
export SPOTIFY_CLIENT_SECRET="your-secret"
```

---

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

- **Python version** — Upgraded from 3.8 to 3.11 (see 2.1.0 for 3.12)
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
- `PlayerStats` — Rank info for all roles
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

