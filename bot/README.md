# 🇸🇪 Mr. Swede

[![CI](https://github.com/jonlee-dev/mr-swede/actions/workflows/ci.yaml/badge.svg)](https://github.com/jonlee-dev/mr-swede/actions/workflows/ci.yaml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.3+-blue.svg)](https://discordpy.readthedocs.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A Swiss-army-knife Discord bot for Overwatch stats tracking and music playback, designed for serverless deployment on Google Cloud Run.

---

## ✨ Features

### 🎮 Overwatch Stats Tracking
- **Multi-account support** — Track your main and all your alt accounts
- **Competitive rank tracking** — View ranks for Tank, Damage, and Support roles
- **Historical stats** — Track your progress over time with Firestore
- **Server leaderboard** — See who's climbing the ranks
- Powered by [Overfast API](https://overfast-api.tekrop.fr/) for reliable data

### 🎵 Music Playback
- **YouTube & Spotify** — Play from URLs or search queries
- **Queue management** — Add, skip, shuffle, and loop tracks
- **Volume control** — Fine-tune your listening experience
- **Auto-disconnect** — Saves resources when inactive

### ☁️ Cloud-Native Design
- **Serverless** — Runs on Cloud Run, scales to zero when idle
- **Secure secrets** — Credentials managed by Google Secret Manager
- **Persistent storage** — Firestore for account data and stats history
- **Health checks** — Built-in `/health` endpoint for monitoring

---

## 🚀 Quick Start

### Prerequisites

- Python 3.12+
- [Poetry](https://python-poetry.org/) for dependency management
- FFmpeg for audio playback
- A Discord bot token ([setup guide](./TODO.md#-discord-developer-portal-setup))

### Installation

```bash
# Clone the repository
git clone https://github.com/jonlee-dev/mr-swede.git
cd mr-swede

# Install Poetry (if needed)
curl -sSL https://install.python-poetry.org | python3 -

# Install dependencies
poetry install

# Configure environment
cp env.example .env
# Edit .env with your credentials (see TODO.md for details)
```

### Running Locally

```bash
# Activate virtual environment
poetry shell

# Run the bot (standalone mode, no HTTP server)
python -m src.main --standalone

# Or run with HTTP health check server (like Cloud Run)
python -m src.main
```

### Running Tests

```bash
# All tests with coverage
poetry run pytest

# Unit tests only (fast)
poetry run pytest tests/unit -v

# Acceptance tests (ATDD)
poetry run pytest tests/acceptance -v

# Generate HTML coverage report
poetry run pytest --cov=src --cov-report=html
open htmlcov/index.html
```

---

## 💬 Interacting with Mr. Swede

Mr. Swede uses Discord's modern **Slash Commands** system. Here's everything you need to know about interacting with the bot.

### How Slash Commands Work

Instead of typing a prefix like `$` or `!`, you interact with Mr. Swede by typing `/` in any text channel where the bot has permissions.

#### Discovering Commands

1. **Type `/`** in any text channel
2. **Look for Mr. Swede** in the command menu that appears
3. **Browse available commands** — Discord shows all commands with descriptions
4. **Click or type** to select a command

#### Using Parameters

Many commands accept parameters (arguments). Discord guides you through them:

```
/ow stats battletag:Player#1234
         ↑
         Parameter name shown, type your value after the colon
```

**Example interactions:**

| What you type | What happens |
|---------------|--------------|
| `/ping` | Bot responds with latency |
| `/ow stats Player#1234` | Shows competitive ranks for that player |
| `/play never gonna give you up` | Searches YouTube and plays the song |
| `/volume 50` | Sets playback volume to 50% |

#### Autocomplete

Some commands offer **autocomplete suggestions** as you type:

- `/help` → Shows category choices: `Overwatch`, `Music`, `General`
- `/loop` → Shows mode choices: `off`, `single`, `queue`

### Response Types

Mr. Swede responds in different ways depending on the command:

| Response Type | Description | Example |
|---------------|-------------|---------|
| **Embed** | Rich formatted message with colors and fields | Stats display, queue list |
| **Ephemeral** | Only visible to you (disappears for others) | Invite link, error messages |
| **Public** | Visible to everyone in the channel | Now playing announcements |

### Voice Channel Interaction

For music commands, you need to be in a voice channel:

1. **Join a voice channel** in your Discord server
2. **Use `/play`** — the bot will join your channel automatically
3. **Control playback** with `/pause`, `/skip`, `/stop`, etc.
4. **Bot auto-disconnects** after 60 seconds of inactivity

> **Tip:** The bot joins *your* voice channel. If you want it in a different channel, join that channel first, then use `/play`.

### Permissions

Commands work based on your Discord permissions:

| Requirement | Commands Affected |
|-------------|-------------------|
| Be in a voice channel | All music commands |
| Same voice channel as bot | `/skip`, `/stop`, `/pause`, `/resume` |
| Server member | All commands (no DM support) |

### Getting Help In-Discord

Don't remember a command? Use the built-in help:

- **`/help`** — Shows all command categories
- **`/help overwatch`** — Shows only Overwatch commands  
- **`/help music`** — Shows only music commands
- **`/info`** — General bot information

---

## 📖 Commands

### Overwatch Commands

| Command | Description |
|---------|-------------|
| `/ow stats <battletag>` | View player competitive stats |
| `/ow track <battletag>` | Start tracking an account |
| `/ow untrack <battletag>` | Stop tracking an account |
| `/ow list` | List your tracked accounts |
| `/ow refresh` | Refresh stats for all your accounts |
| `/ow leaderboard` | Show server ranking leaderboard |

### Music Commands

| Command | Description |
|---------|-------------|
| `/play <query>` | Play a song or add to queue |
| `/pause` | Pause playback |
| `/resume` | Resume playback |
| `/skip` | Skip to next track |
| `/stop` | Stop and clear the queue |
| `/queue` | Show current queue |
| `/nowplaying` | Show current track info |
| `/volume <0-100>` | Set playback volume |
| `/loop <off\|single\|queue>` | Set loop mode |
| `/shuffle` | Shuffle the queue |
| `/leave` | Disconnect from voice channel |

### General Commands

| Command | Description |
|---------|-------------|
| `/ping` | Check bot latency |
| `/help [category]` | Show help information |
| `/info` | Display bot information |
| `/invite` | Get bot invite link |

---

## 🏗️ Architecture

```
mr-swede/
├── src/
│   ├── main.py              # Entry point with FastAPI health check
│   ├── bot.py               # Discord bot setup and configuration
│   ├── config/
│   │   ├── settings.py      # Pydantic settings for non-secret config
│   │   ├── secrets.py       # GSM integration for JSON secrets
│   │   └── logging.py       # Structured logging with structlog
│   ├── cogs/
│   │   ├── general.py       # Utility commands (/ping, /help, etc.)
│   │   ├── overwatch.py     # Overwatch tracking commands
│   │   └── music.py         # Music playback commands
│   ├── services/
│   │   ├── base.py          # Base HTTP client with retry/error handling
│   │   ├── overfast.py      # Overfast API client
│   │   ├── blizzard.py      # Blizzard Battle.net API client
│   │   ├── spotify.py       # Spotify API client
│   │   └── youtube.py       # yt-dlp audio extraction
│   ├── database/
│   │   ├── models.py        # Pydantic models for Firestore
│   │   └── firestore.py     # Async Firestore client
│   └── utils/
│       └── helpers.py       # Utility functions
├── tests/
│   ├── unit/                # Fast unit tests
│   ├── integration/         # API integration tests
│   └── acceptance/          # ATDD with pytest-bdd (Gherkin features)
├── Dockerfile               # Multi-stage build for Cloud Run
├── pyproject.toml           # Poetry configuration
├── env.example              # Environment variable template
├── TODO.md                  # Setup guide & manual tasks
└── CHANGELOG.md             # Release notes
```

---

## ☁️ Deployment

### Cloud Run (Recommended)

The bot is deployed via **Cloud Run's GitHub integration** — push to `main` and it auto-deploys.

Features:
- HTTP health check endpoint for container lifecycle
- **CPU throttling** for cost optimization (only pay when processing)
- Google Secret Manager for secure credential storage
- Firestore for persistent data

### First-Time Setup

1. Connect your GitHub repo to Cloud Run via the [Cloud Console](https://console.cloud.google.com/run)
2. Select your repo and branch (`main`)
3. Cloud Run will build from `Dockerfile` automatically

After first deploy, apply cost-optimized settings:

```bash
gcloud run services update mr-swede \
  --region=us-central1 \
  --cpu-throttling \
  --cpu-boost \
  --memory=512Mi \
  --cpu=1 \
  --min-instances=1 \
  --max-instances=1
```

### Cost Estimate

| Setting | Value | Why |
|---------|-------|-----|
| CPU Throttling | ✅ Enabled | Only pay for CPU when processing commands |
| Min Instances | 1 | Keeps Discord connection alive |
| Max Instances | 1 | No need to scale for personal server |
| Memory | 512Mi | Sufficient for bot + audio |
| CPU | 1 vCPU | Handles audio streaming (throttled when idle) |

**Estimated monthly cost: ~$3-5** (vs ~$35/month without throttling)

> **Note:** The bot maintains a persistent WebSocket connection to Discord, so `min-instances=1` is required. CPU throttling ensures you only pay for actual processing time.

### GitHub Actions (Optional)

You can add a CI workflow (`.github/workflows/ci.yaml`) for:
1. Running linting (Ruff) and type checking (MyPy)
2. Executing unit and acceptance tests
3. Validating PRs before merge

---

## ⚙️ Configuration

### Environment Variables

#### Core Settings

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ENV` | ❌ | `development` | Environment (`development`, `production`) |
| `DEBUG` | ❌ | `false` | Enable debug mode |
| `LOG_LEVEL` | ❌ | `INFO` | Logging level |
| `LOG_FORMAT` | ❌ | `json` | Log format (`console` or `json`) |

#### Discord Settings

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_BOT_NAME` | ❌ | `mr-swede` | Bot name in GSM secrets (`mr-swede` or `ow2-ranked-bot`) |
| `DISCORD_GUILD_ID` | ❌ | — | Guild ID for fast command sync (dev only) |

#### GCP Settings

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GCP_PROJECT_ID` | ❌ | Auto-detected | GCP project ID |
| `FIRESTORE_COLLECTION_PREFIX` | ❌ | `mr_swede_` | Firestore collection prefix |
| `HOST` | ❌ | `0.0.0.0` | Server host (Cloud Run) |
| `PORT` | ❌ | `8080` | Server port (Cloud Run) |

#### Local Development (when GSM unavailable)

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | ✅* | Discord bot token |
| `DISCORD_APPLICATION_ID` | ❌ | Discord application ID |
| `BLIZZARD_CLIENT_ID` | ❌ | Blizzard OAuth client ID |
| `BLIZZARD_CLIENT_SECRET` | ❌ | Blizzard OAuth client secret |
| `BLIZZARD_REGION` | ❌ | API region (`us`, `eu`, `kr`, `tw`) |
| `SPOTIFY_CLIENT_ID` | ❌ | Spotify API client ID |
| `SPOTIFY_CLIENT_SECRET` | ❌ | Spotify API client secret |

> *Required only when running locally without GSM access

### Google Secret Manager

In production, secrets are stored as **JSON objects** in GSM:

#### Secret Structure

```
blizzard-secrets (JSON)
├── client_id
└── client_secret

discord-bot-secrets (JSON)
├── mr-swede.id
├── mr-swede.token
├── mr-swede.public_key
├── ow2-ranked-bot.id
├── ow2-ranked-bot.token
└── ow2-ranked-bot.public_key

spotify-secrets (JSON)
├── client_id
└── client_secret
```

#### Example JSON for `discord-bot-secrets`:

```json
{
  "mr-swede.id": "123456789",
  "mr-swede.token": "your-bot-token",
  "mr-swede.public_key": "your-public-key",
  "ow2-ranked-bot.id": "987654321",
  "ow2-ranked-bot.token": "other-bot-token",
  "ow2-ranked-bot.public_key": "other-public-key"
}
```

The bot automatically loads from GSM in Cloud Run. For local development, set the environment variables above instead

---

## 🧪 Development

### Code Quality

```bash
# Lint with Ruff
poetry run ruff check src tests

# Format with Ruff
poetry run ruff format src tests

# Type check with MyPy
poetry run mypy src

# Run all quality checks
poetry run pre-commit run --all-files
```

### Pre-commit Hooks

Install pre-commit hooks to automatically check code quality:

```bash
poetry run pre-commit install
```

### Test-Driven Development

The project follows ATDD (Acceptance Test-Driven Development):

1. **Feature files** in `tests/acceptance/features/` define behavior in Gherkin
2. **Step definitions** in `tests/acceptance/` implement the scenarios
3. **Unit tests** in `tests/unit/` test individual components

---

## 🛠️ Tech Stack

| Category | Technology |
|----------|------------|
| **Runtime** | Python 3.12 |
| **Discord** | discord.py 2.x with slash commands |
| **Web Framework** | FastAPI (health checks) |
| **Database** | Google Cloud Firestore |
| **Secrets** | Google Secret Manager |
| **Audio** | yt-dlp + FFmpeg |
| **Config** | Pydantic Settings |
| **Logging** | structlog (JSON) |
| **Testing** | pytest, pytest-bdd, pytest-asyncio |
| **Linting** | Ruff, MyPy |
| **CI/CD** | GitHub Actions, Cloud Build |
| **Container** | Docker, Cloud Run |

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Write tests first (TDD!)
4. Make your changes
5. Run quality checks (`poetry run pytest && poetry run ruff check`)
6. Commit your changes (`git commit -m 'Add amazing feature'`)
7. Push to the branch (`git push origin feature/amazing-feature`)
8. Open a Pull Request

---

## 📚 Documentation

- **[TODO.md](./TODO.md)** — Complete setup guide with GCP permissions, API setup, and deployment instructions
- **[CHANGELOG.md](./CHANGELOG.md)** — Version history and release notes

### Note:
Smol Server ID: 635551370761601062
Owner ID: 2584733691276165234