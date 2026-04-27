# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.1.0] - 2026-04-26

### Wire `/valheim *` to GCE + Terraform-manage the bot runtime

Two big landings: the Phase-2 stubs are now real implementations (the
bot actually starts and stops the VM), and the Cloud Run deployment
itself is now Terraform-managed via a new `gcp-bot-runtime` module
instead of click-ops.

### Added

- **`/valheim status|start|stop` actually work.** `services/compute.py`
  implements `describe_instance`/`start_instance`/`stop_instance` against
  `google-cloud-compute`. `services/server_query.py` implements
  `query(host, port)` against `python-a2s`. Both raise typed errors that
  the cog converts into user-facing messages.
- **`infra/modules/gcp-bot-runtime/`** — eight `.tf` files plus a README
  covering: bot SA + project IAM, instance-scoped `compute.instanceAdmin.v1`,
  Discord-secret container + accessor, Artifact Registry repo, Cloud Run
  v2 service, Cloud Build → AR/Run/SA bindings, GitHub-trigger config.
  Wired into `infra/envs/prod/main.tf` after `module.valheim_vm`.
- **`cloudbuild.yaml`** at the repo root — Build → Push → Deploy steps,
  driven by the trigger's substitutions. Replaces the trigger's old
  inline build config that hard-coded `Dockerfile` at the repo root and
  broke when v3.0.0 moved everything into `bot/`.
- **`DISCORD_SECRET_PATH`** env var contract — lets the bot find the
  GSM secret without hardcoding the project number. Set automatically
  by the TF module on the Cloud Run service; documented in both READMEs.
- **`discord_guild_id`** TF variable on the prod env, threaded into the
  bot service. Empty by default (= global slash-command sync); override
  in `terraform.tfvars` for instant per-guild sync during dev.

### Changed

- **Region: us-east4 → us-central1.** The new Terraform-managed Cloud Run
  service is greenfield in us-central1, matching every other resource.
  `cloudbuild.yaml` substitutions flipped accordingly. The old us-east4
  service stays running until cutover; deletion is a manual `gcloud run
  services delete` step (never lived in TF).
- **Bot SA scope tightened.** Compute access is now instance-scoped
  (`compute.instanceAdmin.v1` on the Valheim VM) instead of the
  project-wide binding suggested in TODO.md prior. Secret access stays
  secret-scoped.
- **`__version__` → `3.1.0`** in `bot/src/__init__.py` and
  `bot/pyproject.toml`.
- **Docs.** Dropped the "Phase X" status tables from `README.md` and
  `TODO.md` — phase numbering was a roadmap-tracking concept that
  outlived its usefulness once everything-but-the-idle-watcher shipped.
  `docs/architecture.md` updated to reflect the third TF module and to
  describe `compute.py`/`server_query.py` as real impls (not stubs).
- **`TODO.md`** rewritten around the cutover procedure and the
  `terraform import` step required on first apply (the GSM secret
  pre-existed Terraform, so we adopt-not-create).

### Migration (one-time, on the project that hosts the bot)

1. **Connect Cloud Build to GitHub** in the GCP console
   (`Cloud Build → Triggers → Connect Repository → GitHub`). Required
   prerequisite — TF cannot do the OAuth handshake.
2. `terraform plan` — sanity-check the new `module.bot_runtime` resources.
3. `terraform import module.bot_runtime.google_secret_manager_secret.discord_bot_secrets projects/<project>/secrets/discord-bot-secrets`
   — adopt the existing GSM secret instead of duplicating it.
4. `terraform apply`.
5. Trigger the first build manually
   (`gcloud builds triggers run mr-swede-master --branch=master`) to
   replace the `cloudrun/hello` placeholder image with the real bot.
6. Smoke test `/health` on the new us-central1 service URL, then in
   Discord: `/ping`, `/valheim status`, `/valheim start`.
7. Delete the old us-east4 service + AR repo by hand.

Full instructions in [TODO.md](./TODO.md).

### Out-of-scope

- **No idle watcher yet.** The Valheim VM still bills 24/7 unless
  someone runs `/valheim stop`. The Cloud Function that polls A2S and
  stops the VM after N idle minutes is the next infra module.
- **Backups module** is also still ahead.

---

## [3.0.0] - 2026-04-26

### Pivot to Valheim-only scope

This release strips the bot down to what actually works and rebuilds it
around a single use case: a Discord-controlled, on-demand Valheim server.
Net diff: **+980 / −7,477 lines** across 46 files.

### Removed

- **Overwatch stat tracking** — `cogs/overwatch.py`, `services/overfast.py`, `services/blizzard.py`, all related tests, and the Blizzard/Overfast secret types. Feature didn't work reliably (Overfast rate-limiting + 429s on Cloud Run shared egress IPs).
- **Music playback** — `cogs/music.py`, `services/youtube.py`, `services/spotify.py`, `services/music_player.py`, voice intents, FFmpeg dep in the Dockerfile, and the yt-dlp / spotipy / PyNaCl deps.
- **Firestore** — entire `database/` package. The bot is stateless again; Phase-3 state (if any) goes in GSM or VM-side files.
- **Slash commands** — `/help`, `/invite` (rarely used; Discord's built-in `/` discovery does the same job).
- **Test scaffolding** — `pytest-bdd` + `acceptance/` Gherkin features, `factory-boy`, `hypothesis`, `aioresponses`. Unit tests + (eventual) integration tests are the new line.

### Added

- **`/valheim status|start|stop`** as a `commands.GroupCog` in `cogs/valheim.py`. Returns "Not implemented yet" until Phase 3 wires it.
- **`services/compute.py`** — `InstanceState` dataclass + `describe_instance`/`start_instance`/`stop_instance` stubs. Public surface is three free functions to keep a future provider swap cheap.
- **`services/server_query.py`** — `GameState` dataclass + `query(host, port)` stub. Phase 3 will use `python-a2s` against Valheim's Steam query port (game_port + 1).
- **`http.py`** — FastAPI app with a lifespan that boots Discord *after* uvicorn binds, so Cloud Run health checks pass during the connect window.

### Changed

- **`main.py`** is now ~25 lines — just configure logging and run uvicorn. Discord state lives in `bot.py` + `http.py`.
- **`bot.py`** uses `Intents.default()` only. Privileged intents (members, presences, message_content) are off in code *and* in the Discord developer portal.
- **`/info`** lists the three Valheim subcommands plus `/ping` itself; `__version__` bumped to `3.0.0`.
- **`secrets.py`** — pruned to `DiscordBotSecrets` + `AppSecrets`. Both nested-object (`{"mr-swede": {"token": ...}}`) and dot-notation (`{"mr-swede.token": ...}`) GSM layouts still work.
- **`settings.py`** — added `valheim_zone`, `valheim_instance_name`. Removed `blizzard_*`, `spotify_*`, `firestore_*`.
- **`README.md`** (root + `bot/`), **`TODO.md`**, **`docs/architecture.md`** — refreshed for the new scope.
- **`Dockerfile`** — dropped FFmpeg + `libffi-dev`. Multi-stage poetry-export → pip preserved.
- **`pyproject.toml`** — version `3.0.0`. Pinned `ruff = "^0.1.13"` to match CI. `google-cloud-compute` and `python-a2s` are commented out, ready to uncomment in Phase 3.

### Migration

There's no migration path from 2.x — if you were using Overwatch tracking
or music playback, this release deletes them. Pin to `2.1.12` if you
need them back.

### Why

Most of the 2.x features didn't work in production: Overwatch via
Cloud Run shared egress hit Overfast rate limits constantly, and music
playback over a Cloud Run-hosted bot has chronic websocket-latency
problems (see all the 2.1.x perf entries below). Rather than keep
patching, we pruned. The Valheim use case is what the repo's actually
for now.

---

## [2.1.12] - 2024-12-19

### 🔧 Pre-Initialize BOTH yt-dlp Instances

Fixed the remaining 24-second delay in Phase 1 search by pre-initializing the flat search instance too.

### The Problem

We were pre-initializing the full extraction instance, but still creating a NEW instance for flat search:
```
20:37:02 - get_audio_track START
20:37:26 - Phase 1: Searching YouTube (24 SECONDS later!)
```

The 24s delay was the flat search instance being created on-demand.

### The Fix

Now pre-initialize **TWO** yt-dlp instances during startup:
1. **Full extraction instance** — For getting audio URLs from videos
2. **Flat search instance** — For fast search (extract_flat=True)

### Expected Startup Logs

```
🔧 Initializing yt-dlp instances...
🔧 Creating full extraction instance...
✅ Full extraction instance ready (init_seconds: 25.3)
🔧 Creating flat search instance...
✅ Both yt-dlp instances initialized (total_seconds: 50.1)
```

### Expected Request Logs

```
🔍 Phase 1: Searching YouTube (using pre-initialized instance)...
🎯 SEARCH RESULT (search_ms: 2000)  ← INSTANT!
```

---

## [2.1.11] - 2024-12-19

### ⚡ Two-Phase YouTube Search

Implemented fast two-phase search to dramatically speed up song lookup.

### The Problem

Even with pre-initialized yt-dlp, `ytsearch1:query` with full extraction takes 20-30s because YouTube returns a lot of metadata. This was still causing timeouts.

### The Fix

**Two-phase search:**
1. **Phase 1 (Fast)**: Flat search with `extract_flat=True` (~1-3s) - just get video URL
2. **Phase 2 (Normal)**: Extract audio info from the specific URL (~3-5s)

This is much faster because flat search only gets video IDs/URLs, not full metadata.

### Performance Impact

| Before | After |
|--------|-------|
| Single search: 20-30s | Phase 1: 1-3s + Phase 2: 3-5s |
| Often times out | Total: ~5-8s |

### New Log Messages

```
🔍 Phase 1: Flat search starting...
✅ Phase 1: Got video URL (search_ms: 2000)
🎬 Phase 2: Extracting audio info...
✅ Phase 2: Extraction complete (extract_ms: 4000)
✅ Two-phase search complete (total_ms: 6000)
```

---

## [2.1.10] - 2024-12-19

### 🚀 Pre-Initialize yt-dlp on Startup

Fixed the 50+ second yt-dlp initialization delay on Cloud Run by pre-loading during bot startup.

### The Problem

On Cloud Run with CPU throttling, yt-dlp initialization (loading 1000+ extractors) takes 30-50 seconds when CPU is throttled. This caused:
- Requests timing out at 25s while yt-dlp was still initializing
- "Interaction expired" errors from Discord
- Bot appearing unresponsive

### The Fix

- **Pre-initialize yt-dlp during startup** — `preload_cookies()` now also calls `preload_ytdl()` to initialize yt-dlp while CPU is active
- **Reuse yt-dlp instance** — Single global instance reused across all requests (no per-request initialization)
- **Faster startup options** — Added `cachedir: False`, `no_color: True` to speed up initialization

### Added

- **`preload_ytdl()`** — New async function to pre-initialize yt-dlp in background
- **Global yt-dlp instance** — `_ytdl_instance` reused across all extractions
- **Startup logging** — Shows yt-dlp initialization time during startup

### Performance Impact

| Before | After |
|--------|-------|
| 30-50s first request | ~1-5s (search only) |
| New instance per request | Single reused instance |

---

## [2.1.9] - 2024-12-19

### 🔧 Format Selection Hotfix

Fixed "Requested format is not available" error for some YouTube videos.

### Fixed

- **Format string too restrictive** — Changed from `worstaudio[acodec=opus]/...` to `bestaudio/best` for better compatibility
- Some videos (e.g., Gangnam Style) don't have opus/vorbis streams, causing extraction to fail

### Changed

- **yt-dlp format** — Now uses `bestaudio/best` (more reliable) instead of restrictive codec filters
- **Timeout reduced** — yt-dlp timeout reduced from 30s to 25s

---

## [2.1.8] - 2024-12-19

### ⚡ Improved Music Playback

This release improves logging and response time for music playback.

### Changed

- **Instant feedback** — "Now Playing" message sent before FFmpeg initializes
- **Background playback** — Playback starts in background task, doesn't block response
- **Reduced timeout** — yt-dlp timeout reduced from 60s to 30s

### Added

- **Detailed extraction logging** — Shows timing for each phase (init, search, format selection)
- **Format selection logging** — Shows which audio codec/bitrate was chosen
- **Cache hit/miss logging** — Clear indication of audio URL cache status

---

## [2.1.7] - 2024-12-19

### 💾 Firestore-Based Stats Caching

This release replaces in-memory caching with Firestore for Overfast stats, making the cache persist across container restarts.

### Added

- **Firestore stats cache** — Stats are now cached in Firestore (`mr_swede_stats_cache` collection)
- **1-hour cache TTL** — Stats cached for 1 hour (persists across deployments/restarts)
- **`get_cached_stats()` method** — FirestoreClient method to retrieve cached stats
- **`cache_stats()` method** — FirestoreClient method to store stats

### Changed

- **Cache persistence** — Moved from in-memory dict to Firestore (survives restarts)
- **Cache TTL** — Increased from 10 minutes to 1 hour to reduce API calls
- **Cache key format** — Uses `battletag.lower().replace("#", "-")` for consistency

### Removed

- **In-memory stats cache** — Replaced with Firestore-based caching

### Technical Notes

- **Audio is NOT stored locally** — `skip_download=True` means we only extract URLs; FFmpeg streams directly from YouTube
- **No persistence issues for music** — Audio streams directly from YouTube to Discord, no local storage

---

## [2.1.6] - 2024-12-19

### 🐛 Timeout Error Handling & Heartbeat Fix

This release fixes the "No Results" error shown when extraction times out, and reduces timeouts to prevent heartbeat blocking.

### Fixed

- **"No Results" shown on timeout** — Now correctly shows "Request Timed Out" when YouTube extraction times out
- **Heartbeat blocked warnings** — Reduced yt-dlp timeout from 60s to 30s to prevent blocking Discord heartbeats
- **Rate limit lock deadlock** — Added 5s timeout on rate limit lock acquisition
- **Clearer 429 error messages** — Explains Cloud Run shared IP issue when Overfast API rate limits

### Changed

- **yt-dlp timeout** — Reduced from 60s to 30s for faster failure
- **Socket timeout** — Reduced from 10s to 8s for faster response
- **Rate limit interval** — Increased from 1.5s to 2s for Overfast API
- **Rate limit logging** — Demoted from `info` to `debug` to reduce log noise
- **TimeoutError propagation** — `get_audio_track()` now raises `TimeoutError` instead of returning `None`

### Added

- **Custom User-Agent for Overfast** — `MrSwedeBot/2.1` to potentially avoid shared rate limiting
- **Rate limit header logging** — Logs `Retry-After` and rate limit headers on 429 errors
- **Request timing logs** — Shows seconds since last request to help debug rate limiting

---

## [2.1.5] - 2024-12-19

### 🧪 Comprehensive Test Coverage

This release adds 27 new unit tests covering all recent functionality.

### Added

- **Overfast rate limiting tests** — Tests for `_wait_for_rate_limit` function behavior
- **Overwatch caching tests** — Tests for `_get_cached_stats` and `_cache_stats`
- **YouTube caching tests** — Tests for audio URL caching with TTL expiration
- **YouTube options tests** — Tests for low-quality format preference and retry disabling
- **Thread pool tests** — Tests for FFmpeg and yt-dlp executor isolation
- **Cache key tests** — Tests for YouTube URL normalization and query lowercasing
- **AudioTrack tests** — Tests for duration formatting

### Changed

- Test count increased from 58 to 85 tests
- Overall code coverage improved from 26% to 35%

---

## [2.1.4] - 2024-12-19

### 📋 Enhanced Debug Logging

This release adds comprehensive logging for debugging music and Overwatch commands.

### Added

- **Music cog logging** — Full request lifecycle logging for `/play` command:
  - Command start with user/guild info
  - Voice channel connection status
  - Spotify URL parsing details
  - YouTube search/extraction timing
  - FFmpeg source creation timing
  - Playback start confirmation
- **Overwatch cog logging** — Full request lifecycle logging for `/ow stats` command:
  - Command start with user/guild info
  - Cache hit/miss/expired status with TTL info
  - API request timing
  - Command completion timing
- **YouTube service logging** — Detailed extraction logs:
  - Cache hit/miss/expired with TTL
  - Search vs direct URL extraction
  - Per-operation timing (search, extract, overall)
  - Error categorization (auth, unavailable, timeout)
- **Overfast service logging** — API request lifecycle:
  - Rate limit wait times
  - Request start/success/failure with timing
  - Response keys for debugging

### Changed

- **Log level adjustments** — Cache and rate limit logs promoted from debug to info for better visibility

---

## [2.1.3] - 2024-12-19

### 🎵 Lower Audio Quality & Overfast Rate Limiting Fix

This release optimizes audio extraction and fixes Overfast API rate limiting.

### Changed

- **Lower audio quality extraction** — Now requests lowest quality opus/vorbis instead of "best" (Discord only supports 64kbps anyway)
- **Audio format selection** — Prefers lowest bitrate formats for faster downloads
- **Cache TTL increased** — Overfast stats cached for 10 minutes (up from 5) due to strict rate limits
- **Overfast API timeout** — Reduced to 15 seconds (was 30)

### Fixed

- **Overfast API 429 errors** — Added global rate limiter (1.5s between requests) to ALL Overfast API calls
- **Rate limiting in refresh** — Added 1.5s delay between accounts when refreshing stats
- **Track command hitting API** — Now checks cache before making API call
- **Missing cache update** — Refresh command now updates the in-memory cache

### Added

- **Global Overfast rate limiter** — Thread-safe lock ensures all requests are spaced 1.5s apart
- **Rate limit feedback** — Refresh command now shows how many accounts were rate-limited

---

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

