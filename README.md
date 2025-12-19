# 🇸🇪 Mr. Swede

A Swiss-army-knife Discord bot for Overwatch stats tracking and music playback, deployed on Google Cloud Run.

## Features

### 🎮 Overwatch Stats Tracking
- Track multiple accounts (including alts)
- View competitive ranks for all roles (Tank, Damage, Support)
- Historical stats tracking with Firestore
- Server leaderboard
- Uses [Overfast API](https://overfast-api.tekrop.fr/) for reliable data

### 🎵 Music Playback
- Play music from YouTube URLs or search queries
- Spotify URL support (searches YouTube for playback)
- Queue management (add, skip, shuffle, loop)
- Volume control
- Auto-disconnect when inactive

### 🔧 General
- Modern slash commands
- Health check endpoint for Cloud Run
- Structured logging with JSON output
- Google Secret Manager integration

## Quick Start

### Prerequisites
- Python 3.11+
- [Poetry](https://python-poetry.org/) for dependency management
- FFmpeg for audio playback
- A Discord bot token

### Local Development

```bash
# Install dependencies
poetry install

# Copy environment file
cp env.example .env
# Edit .env with your credentials

# Run the bot
poetry run python -m src.main --standalone
```

### Running Tests

```bash
# All tests
poetry run pytest

# Unit tests only
poetry run pytest tests/unit -v

# With coverage
poetry run pytest --cov=src --cov-report=html
```

## Commands

### Overwatch
| Command | Description |
|---------|-------------|
| `/ow stats <battletag>` | View player stats |
| `/ow track <battletag>` | Start tracking an account |
| `/ow untrack <battletag>` | Stop tracking an account |
| `/ow list` | List your tracked accounts |
| `/ow refresh` | Refresh all your stats |
| `/ow leaderboard` | Server ranking leaderboard |

### Music
| Command | Description |
|---------|-------------|
| `/play <query>` | Play a song or add to queue |
| `/pause` | Pause playback |
| `/resume` | Resume playback |
| `/skip` | Skip current track |
| `/stop` | Stop and clear queue |
| `/queue` | Show the queue |
| `/volume <0-100>` | Set volume |
| `/loop <off/single/queue>` | Set loop mode |
| `/shuffle` | Shuffle the queue |
| `/leave` | Disconnect from voice |
| `/nowplaying` | Show current track |

### General
| Command | Description |
|---------|-------------|
| `/ping` | Check bot latency |
| `/help` | Show help |
| `/info` | Bot information |

## Architecture

```
mr-swede/
├── src/
│   ├── main.py           # Entry point with FastAPI health check
│   ├── bot.py            # Discord bot setup
│   ├── config/           # Settings & logging
│   ├── cogs/             # Discord command modules
│   │   ├── general.py    # Utility commands
│   │   ├── overwatch.py  # OW stats commands
│   │   └── music.py      # Music commands
│   ├── services/         # External API clients
│   │   ├── overfast.py   # Overfast API
│   │   ├── blizzard.py   # Blizzard API
│   │   ├── spotify.py    # Spotify API
│   │   └── youtube.py    # yt-dlp wrapper
│   ├── database/         # Firestore integration
│   └── utils/            # Helper functions
├── tests/
│   ├── unit/             # Unit tests
│   ├── integration/      # Integration tests
│   └── acceptance/       # ATDD tests (pytest-bdd)
├── Dockerfile            # Container image
├── cloudbuild.yaml       # Cloud Build config
└── pyproject.toml        # Poetry config
```

## Deployment

### Google Cloud Run

The bot is designed for Cloud Run deployment with:
- HTTP health check endpoint (`/health`)
- Automatic scaling (min 1 instance for voice features)
- Google Secret Manager for credentials
- Firestore for data persistence

See [TODO.md](./TODO.md) for detailed setup instructions.

### Quick Deploy

```bash
# First time setup - see TODO.md for prerequisites

# Deploy with Cloud Build
gcloud builds submit --config=cloudbuild.yaml
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DISCORD_TOKEN` | Discord bot token | Required |
| `DISCORD_GUILD_ID` | Guild for fast command sync | Optional |
| `GCP_PROJECT_ID` | GCP project ID | Auto-detected |
| `USE_GSM` | Use Google Secret Manager | `true` |
| `BLIZZARD_CLIENT_ID` | Blizzard API client ID | Required for deck features |
| `SPOTIFY_CLIENT_ID` | Spotify API client ID | Required for Spotify URLs |
| `LOG_LEVEL` | Logging level | `INFO` |
| `LOG_FORMAT` | Log format (console/json) | `json` |

### Secrets in Google Secret Manager

- `discord-token`
- `blizzard-client-id`
- `blizzard-client-secret`
- `spotify-client-id`
- `spotify-client-secret`

## Development

### Code Quality

```bash
# Lint
poetry run ruff check src tests

# Format
poetry run ruff format src tests

# Type check
poetry run mypy src
```

### Test-Driven Development

The project uses ATDD with pytest-bdd. Feature files are in `tests/acceptance/features/`:
- `overwatch.feature` - Overwatch command scenarios
- `music.feature` - Music command scenarios

## Tech Stack

- **Runtime**: Python 3.11
- **Discord**: discord.py 2.x with slash commands
- **Web Framework**: FastAPI (for health checks)
- **Database**: Google Firestore
- **Secrets**: Google Secret Manager
- **Audio**: yt-dlp + FFmpeg
- **Testing**: pytest + pytest-bdd + pytest-asyncio
- **CI/CD**: GitHub Actions + Cloud Build

## License

MIT

## Contributing

1. Fork the repository
2. Create a feature branch
3. Write tests (TDD!)
4. Make your changes
5. Run `poetry run pytest` and `poetry run ruff check`
6. Submit a pull request
