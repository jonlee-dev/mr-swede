# Mr. Swede

Discord-controlled Valheim server. Slash commands start, stop, and check status of a Google Compute Engine VM that hosts the game server.

The bot runs on Cloud Run with `min-instances=1` (Discord requires a persistent gateway connection). The Valheim VM is on-demand: idle most of the time, woken via `/valheim start`.

---

## Layout

```
bot/
├── src/
│   ├── main.py            # Uvicorn launcher
│   ├── http.py            # FastAPI app + lifespan + /health endpoint
│   ├── bot.py             # Discord bot (cog loader, error handler, intents)
│   ├── config/
│   │   ├── settings.py    # Pydantic settings (env + .env)
│   │   ├── secrets.py     # GSM client (Discord token)
│   │   └── logging.py     # structlog setup
│   ├── cogs/
│   │   ├── diagnostics.py # /ping, /info
│   │   └── valheim.py     # /valheim status|start|stop  (Phase 3 stubs)
│   ├── services/
│   │   ├── compute.py     # GCE start/stop/describe (Phase 3 stubs)
│   │   └── server_query.py # Steam A2S query        (Phase 3 stubs)
│   └── utils/helpers.py
├── tests/
│   ├── unit/              # Fast, hermetic tests
│   └── conftest.py
├── Dockerfile             # Multi-stage build for Cloud Run
└── pyproject.toml
```

---

## Local development

All commands below run from `bot/`.

```bash
poetry install
gcloud auth application-default login   # so GSM lookups work locally

# Either set DISCORD_TOKEN in .env, or rely on GSM. Then:
poetry run python -m src.main
# Open http://localhost:8080/health to see bot connection state.
```

`DISCORD_GUILD_ID` is worth setting during dev — slash commands sync to one guild instantly, vs ~1hr globally.

### Tests

```bash
poetry run pytest                        # all tests with coverage
poetry run pytest tests/unit -v          # unit-only, fast
poetry run ruff check src tests
poetry run ruff format src tests
poetry run mypy src
```

---

## Deployment

Cloud Run, deployed from `master` via GitHub integration. Build context is this `bot/` directory.

After first deploy, apply cost-optimized settings (one-shot):

```bash
gcloud run services update mr-swede \
  --region=us-central1 \
  --cpu-throttling --cpu-boost \
  --memory=512Mi --cpu=1 \
  --min-instances=1 --max-instances=1 \
  --timeout=3600 \
  --set-env-vars="ENV=production,LOG_FORMAT=json,DISCORD_BOT_NAME=mr-swede,VALHEIM_INSTANCE_NAME=valheim-server,VALHEIM_ZONE=us-central1-a"
```

`min-instances=1` is mandatory — Discord disconnects sessions that idle for more than ~60s, and the Cloud Run CPU-throttled cost of one warm instance is ~$3-5/month.

---

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `ENV` | `development` | `production` toggles JSON logs and skips `.env` loading |
| `DISCORD_BOT_NAME` | `mr-swede` | Key into the `discord-bot-secrets` JSON |
| `DISCORD_GUILD_ID` | _(unset)_ | If set, slash commands sync to this guild only |
| `GCP_PROJECT_ID` | auto-detect | Cloud Run sets `GOOGLE_CLOUD_PROJECT`, picked up automatically |
| `VALHEIM_ZONE` | `us-central1-a` | Where the Valheim VM lives |
| `VALHEIM_INSTANCE_NAME` | `valheim-server` | GCE instance name to control |
| `DISCORD_TOKEN` | _(unset)_ | Local-dev fallback when GSM is unreachable |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | Cloud Run sets `PORT` |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | `console` for local dev |

### Secrets

The Discord token comes from a GSM secret named `discord-bot-secrets`. The secret holds a JSON object keyed by bot name, e.g.:

```json
{ "mr-swede": { "id": "...", "token": "...", "public_key": "..." } }
```

Dot-notation flat keys (`"mr-swede.token": "..."`) are also accepted for backwards compatibility with the existing secret structure.

---

## Commands

| Command | Status | What it does |
|---|---|---|
| `/ping` | implemented | Latency check |
| `/info` | implemented | Bot version + command list |
| `/valheim status` | scaffolded | Will report VM state + player count once Phase 3 lands |
| `/valheim start` | scaffolded | Will start the GCE VM |
| `/valheim stop` | scaffolded | Will stop the GCE VM |

The `/valheim` commands currently respond with "not implemented yet". Phase 3 wires them to `src.services.compute` and `src.services.server_query`.

---

## Tech stack

| Category | Tech |
|---|---|
| Runtime | Python 3.12 |
| Bot framework | discord.py 2.x (slash commands only) |
| HTTP | FastAPI + uvicorn (Cloud Run health checks) |
| Cloud | Google Cloud Run + Secret Manager + Compute Engine |
| Config | Pydantic Settings |
| Logging | structlog (JSON in prod) |
| Testing | pytest + pytest-asyncio |
| Linting | Ruff + MyPy |

See [../docs/architecture.md](../docs/architecture.md) for the cross-component picture and [../docs/runbook.md](../docs/runbook.md) for failure scenarios.
