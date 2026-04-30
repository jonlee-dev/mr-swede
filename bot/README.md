# Mr. Swede

Multi-feature Discord bot. Today: an on-demand Valheim game server (`/valheim *`) and a Lavalink-backed music player (`/music *`). The cog architecture is built so adding the next feature is just dropping in another cog.

The bot runs on Cloud Run with `min-instances=1` (Discord requires a persistent gateway connection). Both the Valheim and Lavalink VMs are on-demand GCE instances: idle most of the time, woken on first `/valheim start` or `/music play`, auto-stopped by the idle watcher after ~60-90 min of inactivity.

---

## Layout

```
bot/
├── src/
│   ├── main.py            # Uvicorn launcher
│   ├── http.py            # FastAPI app + lifespan + /health endpoint
│   ├── bot.py             # Discord bot (cog loader, error handler, intents incl. voice)
│   ├── config/
│   │   ├── settings.py    # Pydantic settings (env + .env)
│   │   ├── secrets.py     # GSM client (Discord token, Valheim password, Lavalink password)
│   │   └── logging.py     # structlog setup
│   ├── cogs/
│   │   ├── diagnostics.py # /ping, /info
│   │   ├── valheim.py     # /valheim status|start|stop
│   │   └── music.py       # /music play|skip|pause|resume|stop|queue|nowplaying|...
│   ├── services/
│   │   ├── compute.py     # GCE start/stop/describe via google-cloud-compute
│   │   ├── server_query.py # HTTP fetch of /status.json from the Valheim VM's daemon
│   │   └── music.py       # Wavelink wrapper (node connect, search, play, idempotent)
│   └── utils/
│       ├── checks.py      # @requires_channel decorator (gates /music to #bot-spam)
│       └── helpers.py
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

### Tests + quality gates

```bash
poetry run pytest                        # all tests with coverage
poetry run pytest tests/unit -v          # unit-only, fast
poetry run ruff check src tests
poetry run ruff format src tests
poetry run mypy src
```

Or run everything CI runs in one shot:

```bash
make check                               # ruff format-check, ruff check, mypy, pytest, poetry-lock sync
make fix                                 # ruff format + ruff check --fix (autofixes)
```

### Pre-commit hooks (one-time setup)

The repo ships a `.pre-commit-config.yaml` at the root. Install both
the per-commit and per-push hooks once:

```bash
poetry run pre-commit install --hook-type pre-commit --hook-type pre-push
```

After that:
- **on commit**: ruff format + lint + mypy run on the staged files.
- **on push**: `poetry.lock` sync check + `pytest tests/unit` run.

Skipping is `git commit --no-verify` / `git push --no-verify` if you
need to. CI runs the same gates regardless.

---

## Deployment

Cloud Run, deployed from `master` via GitHub integration. Build context is this `bot/` directory.

After first deploy, apply cost-optimized settings (one-shot):

```bash
gcloud run services update mr-swede \
  --region=us-central1 \
  --no-cpu-throttling --cpu-boost \
  --memory=512Mi --cpu=1 \
  --min-instances=1 --max-instances=1 \
  --timeout=3600 \
  --set-env-vars="ENV=production,LOG_FORMAT=json,DISCORD_BOT_NAME=mr-swede,VALHEIM_INSTANCE_NAME=valheim-server,VALHEIM_ZONE=us-central1-a"
```

`min-instances=1` is mandatory — Discord disconnects sessions that idle for more than ~60s. CPU is allocated continuously (`--no-cpu-throttling`) because the bot does most of its work over the Discord WebSocket gateway, not over Cloud Run's HTTP port; throttled mode starves outbound TLS handshakes from `/valheim *` calls. Cost: ~$15-20/month.

---

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `ENV` | `development` | `production` toggles JSON logs and skips `.env` loading |
| `DISCORD_BOT_NAME` | `mr-swede` | Key into the `discord-bot-secrets` JSON |
| `DISCORD_GUILD_ID` | _(unset)_ | If set, slash commands sync to this guild only |
| `GCP_PROJECT_ID` | auto-detect | Cloud Run sets `GOOGLE_CLOUD_PROJECT`, picked up automatically |
| `VALHEIM_ZONE` | `us-central1-a` | Where the Valheim VM lives |
| `VALHEIM_INSTANCE_NAME` | `valheim-server` | Valheim GCE instance name to control |
| `LAVALINK_ZONE` | `us-central1-a` | Where the Lavalink VM lives |
| `LAVALINK_INSTANCE_NAME` | `lavalink-server` | Lavalink GCE instance name to control |
| `LAVALINK_HOST` | _(auto-resolve)_ | Lavalink host. Empty = resolve VM public IP at runtime; set explicitly for local dev. |
| `LAVALINK_PORT` | `2333` | Lavalink REST/WS port |
| `MUSIC_COMMAND_CHANNEL_ID` | _(unset)_ | Channel where `/music *` is allowed (defaults to denied if unset) |
| `DISCORD_SECRET_PATH` | _(auto-built)_ | Full GSM resource path of the Discord secret. Set by Terraform on the Cloud Run service. Locally, the bot constructs `projects/<GCP_PROJECT_ID>/secrets/discord-bot-secrets/versions/latest` if unset. |
| `DISCORD_TOKEN` | _(unset)_ | Local-dev fallback when GSM is unreachable |
| `LAVALINK_PASSWORD` | _(unset)_ | Local-dev fallback when GSM is unreachable; production reads `lavalink-server-password` from GSM |
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

| Command | What it does |
|---|---|
| `/ping` | Latency check |
| `/info` | Bot version + per-feature command list |
| `/valheim status` | Reports VM state, PlayFab join code, server password, and player count |
| `/valheim start` | Starts the Valheim GCE VM (idempotent — safe if already running) |
| `/valheim stop` | Stops the Valheim GCE VM (idempotent) |
| `/music play <query>` | Auto-starts the Lavalink VM, joins your VC, plays a YouTube/SoundCloud/HTTP query |
| `/music skip` / `pause` / `resume` / `stop` | Playback control (`stop` clears queue + leaves) |
| `/music queue` / `nowplaying` | Inspect what's playing |
| `/music volume <0-100>` / `shuffle` / `loop <off\|track\|queue>` | Tune playback |

The `/valheim` commands call into `src.services.compute` (start/stop/describe via `google-cloud-compute`) and `src.services.server_query` (HTTP fetch of `/status.json` from the VM's log-scraping daemon at `server/scripts/status-server.py`).

The `/music` commands call into `src.services.music` (Wavelink wrapper around Lavalink) plus `src.services.compute` for the auto-start. They're gated by `src.utils.checks.requires_channel("music_command_channel_id")`, which is a thin `app_commands.check` that compares `interaction.channel_id` against `settings.music_command_channel_id`. The bot joins whichever voice channel the invoking user is currently in.

---

## Tech stack

| Category | Tech |
|---|---|
| Runtime | Python 3.12 |
| Bot framework | discord.py[voice] 2.x (slash commands only; PyNaCl for voice) |
| Music client | Wavelink 3.5.x → Lavalink 4.2.x (Java, on a separate GCE VM) |
| HTTP | FastAPI + uvicorn (Cloud Run health checks) |
| Cloud | Google Cloud Run + Secret Manager + Compute Engine |
| Config | Pydantic Settings |
| Logging | structlog (JSON in prod) |
| Testing | pytest + pytest-asyncio |
| Linting | Ruff + MyPy |

See [../docs/architecture.md](../docs/architecture.md) for the cross-component picture and [../docs/runbook.md](../docs/runbook.md) for failure scenarios.
