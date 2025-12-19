# 🇸🇪 Mr. Swede

[![CI](https://github.com/jonlee-dev/mr-swede/actions/workflows/ci.yaml/badge.svg)](https://github.com/jonlee-dev/mr-swede/actions/workflows/ci.yaml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
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

- Python 3.11+
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
│   │   ├── settings.py      # Pydantic settings with GSM integration
│   │   └── logging.py       # Structured logging with structlog
│   ├── cogs/
│   │   ├── general.py       # Utility commands (/ping, /help, etc.)
│   │   ├── overwatch.py     # Overwatch tracking commands
│   │   └── music.py         # Music playback commands
│   ├── services/
│   │   ├── base.py          # Base HTTP client with OAuth support
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
├── cloudbuild.yaml          # GCP Cloud Build CI/CD pipeline
├── pyproject.toml           # Poetry configuration
├── TODO.md                  # Setup guide & manual tasks
└── CHANGELOG.md             # Release notes
```

---

## ☁️ Deployment

### Cloud Run (Recommended)

The bot is optimized for Google Cloud Run with:
- HTTP health check endpoint for container lifecycle
- Automatic scaling (configurable min/max instances)
- Google Secret Manager for secure credential storage
- Firestore for persistent data

```bash
# First-time setup: follow TODO.md for prerequisites

# Deploy using Cloud Build
gcloud builds submit --config=cloudbuild.yaml
```

> **Note:** For voice features to work reliably, set `min-instances=1` in Cloud Run configuration. This incurs a baseline cost but ensures the bot can maintain voice connections.

### GitHub Actions

The repository includes a CI workflow (`.github/workflows/ci.yaml`) that:
1. Runs linting (Ruff) and type checking (MyPy)
2. Executes unit and acceptance tests
3. Builds Docker image on main branch pushes

---

## ⚙️ Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | ✅ | Discord bot token |
| `DISCORD_GUILD_ID` | ❌ | Guild ID for fast command sync (dev) |
| `GCP_PROJECT_ID` | ❌ | GCP project (auto-detected on Cloud Run) |
| `USE_GSM` | ❌ | Use Secret Manager for secrets (default: `true`) |
| `BLIZZARD_CLIENT_ID` | ❌ | For Blizzard API features |
| `BLIZZARD_CLIENT_SECRET` | ❌ | For Blizzard API features |
| `SPOTIFY_CLIENT_ID` | ❌ | For Spotify URL support |
| `SPOTIFY_CLIENT_SECRET` | ❌ | For Spotify URL support |
| `LOG_LEVEL` | ❌ | Logging level (default: `INFO`) |
| `LOG_FORMAT` | ❌ | `console` or `json` (default: `json`) |

### Google Secret Manager

When `USE_GSM=true`, the bot loads secrets from GSM with these names:
- `discord-token`
- `blizzard-client-id`
- `blizzard-client-secret`
- `spotify-client-id`
- `spotify-client-secret`

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
| **Runtime** | Python 3.11 |
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
