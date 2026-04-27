# Mr. Swede

[![CI](https://github.com/jonlee-dev/mr-swede/actions/workflows/ci.yaml/badge.svg)](https://github.com/jonlee-dev/mr-swede/actions/workflows/ci.yaml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.3+-blue.svg)](https://discordpy.readthedocs.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A Discord bot that controls an on-demand Valheim server. Runs on Cloud Run; the game server runs on GCE and only spins up when someone asks for it. Repo also contains the Terraform that builds the VM and the runtime files that live inside it.

> **History.** This bot used to track Overwatch stats and play music. Both features are gone — see [CHANGELOG.md](./CHANGELOG.md) v3.0.0 for the prune. The current scope is `/valheim status|start|stop` only.

---

## Repository layout

| Path | Contents |
|---|---|
| [`bot/`](bot/) | Python (discord.py) bot — Cloud Run service. Slash-only. |
| [`infra/`](infra/) | Terraform for all GCP resources (bot runtime, Valheim VM, backups, idle watcher). |
| [`server/`](server/) | Files that run *inside* the Valheim VM — docker-compose, startup-script, ops scripts. |
| [`docs/`](docs/) | Architecture diagram, bootstrap procedure, runbook. |

Bot-related Poetry / pytest / Docker commands run from inside [`bot/`](bot/). Terraform commands run from `infra/envs/prod/`.

---

## Status

The bot is fully functional. `/valheim status|start|stop` is wired to GCE and a log-scraping HTTP daemon on the VM. The GCP infra is fully Terraform-managed across four modules:

- [`gcp-bootstrap`](infra/modules/gcp-bootstrap) — APIs, state bucket, Workload Identity Federation
- [`gcp-valheim-vm`](infra/modules/gcp-valheim-vm) — VPC, firewall, persistent disk, VM, server-password GSM secret
- [`gcp-bot-runtime`](infra/modules/gcp-bot-runtime) — Cloud Run service, Artifact Registry repo, Cloud Build trigger, IAM, Discord-secret container
- [`gcp-idle-watcher`](infra/modules/gcp-idle-watcher) — Cloud Function + Scheduler that polls the VM's `/status.json` endpoint and stops the VM after N consecutive empty checks (default: ~60-90 min idle window)

See [TODO.md](./TODO.md) for the cutover checklist and the manual prerequisites (Discord developer portal, Cloud Build ↔ GitHub OAuth).

---

## Commands

| Command | Description |
|---|---|
| `/ping` | Latency check |
| `/info` | Bot version + loaded cog list |
| `/valheim status` | Show VM state, PlayFab join code, server password, and player count |
| `/valheim start` | Start the Valheim VM (idempotent) |
| `/valheim stop` | Stop the Valheim VM (idempotent) |

---

## Quick start (local development)

```bash
git clone https://github.com/jonlee-dev/mr-swede.git
cd mr-swede/bot

poetry install
cp env.example .env                       # edit DISCORD_TOKEN if no GSM access
gcloud auth application-default login     # for GSM lookups (skip if using DISCORD_TOKEN)

poetry run python -m src.main
```

The bot starts a FastAPI server on port 8080 (for Cloud Run health checks) and connects to Discord in the background. If you don't have GSM access, set `DISCORD_TOKEN` in `bot/.env` to bypass GSM entirely.

### Running tests

```bash
cd bot
poetry run pytest                     # all tests
poetry run pytest tests/unit -v       # unit tests only
poetry run pytest --cov=src           # with coverage
```

### Code quality

```bash
cd bot
poetry run ruff check src tests       # lint
poetry run ruff format src tests      # format
poetry run mypy src                   # type check
```

---

## Deployment

The bot deploys via Cloud Build → Cloud Run, all Terraform-managed:

```bash
cd infra/envs/prod
terraform plan
terraform apply
```

On `terraform apply`, [`infra/modules/gcp-bot-runtime`](infra/modules/gcp-bot-runtime) creates the Cloud Run service (with a `cloudrun/hello` placeholder image), the Artifact Registry repo, and the Cloud Build trigger. The first push to `master` (or `gcloud builds triggers run mr-swede-master --branch=master`) replaces the placeholder with the real bot image.

CI runs `fmt → validate → plan` on every PR touching `infra/**` and runs `apply` on merge to `master` (gated by the `prod` GitHub Environment). Auth is via Workload Identity Federation — no JSON keys.

**First-apply prerequisites and the `discord-bot-secrets` import step are documented in [TODO.md](./TODO.md#first-time-gcp-setup).** Reading that section before the first `terraform apply` is mandatory; the import has to happen between `plan` and `apply` or TF will try to create a duplicate of an already-existing GSM secret.

### Cost estimate

| Component | ~Monthly |
|---|---|
| Cloud Run bot (min-instances=1, CPU always-on) | $15–20 |
| Valheim VM (e2-standard-2, stopped most of the time) | $5–10 |
| Persistent disk (20GB pd-balanced) | ~$2 |
| Snapshots + GCS backups | <$1 |
| **Total** | **~$22–33** |

The two big costs are the bot's always-on CPU and the VM running 24/7. The idle watcher cuts the VM bill by ~70-80% in practice — it stops the VM after 60-90 minutes of zero players, so the cost table assumes ~3 hours of daily usage rather than 24/7. The bot uses `cpu_idle = false` because Discord delivers slash commands over a WebSocket gateway (not over Cloud Run's HTTP port) — a CPU-throttled service starves the worker thread doing TLS handshakes from `/valheim *` calls. See [`infra/modules/gcp-bot-runtime/service.tf`](infra/modules/gcp-bot-runtime/service.tf) for the full reasoning.

---

## Configuration

### Environment variables (non-secret)

| Variable | Default | Description |
|---|---|---|
| `ENV` | `development` | `development` or `production` |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_FORMAT` | `json` | `json` or `console` |
| `DISCORD_BOT_NAME` | `mr-swede` | Key into the GSM `discord-bot-secrets` JSON |
| `DISCORD_GUILD_ID` | — | If set, slash commands sync to this guild only (instant). Empty = global sync (~1hr). |
| `GCP_PROJECT_ID` | auto-detected | GCP project ID |
| `VALHEIM_ZONE` | `us-central1-a` | Compute zone of the Valheim VM |
| `VALHEIM_INSTANCE_NAME` | `valheim-server` | Instance name to control |
| `DISCORD_SECRET_PATH` | auto-built from `GCP_PROJECT_ID` | Full GSM secret resource path (`projects/<num>/secrets/discord-bot-secrets/versions/latest`). Cloud Run gets this from Terraform; locally the bot builds a default. |
| `HOST` | `0.0.0.0` | HTTP server bind address |
| `PORT` | `8080` | HTTP server port |

### Local-dev overrides (skip GSM)

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Discord bot token — when set, GSM lookup is skipped |
| `DISCORD_APPLICATION_ID` | Discord application ID |

### Google Secret Manager

In production, the bot reads its Discord token from a single GSM secret named `discord-bot-secrets` containing JSON of the form:

```json
{
  "mr-swede": {
    "id": "123456789",
    "token": "your-bot-token",
    "public_key": "your-public-key"
  }
}
```

The nested-object form above is preferred. The bot also accepts dot-notation keys (`"mr-swede.token": "..."`) for backwards compatibility — see [bot/src/config/secrets.py](bot/src/config/secrets.py) for both lookup paths. The Valheim server password lives in a separate `valheim-server-password` secret seeded out-of-band (see [docs/bootstrap.md](docs/bootstrap.md)).

---

## Architecture

```
mr-swede/
├── bot/                                 # Cloud Run service (Python, discord.py)
│   ├── src/
│   │   ├── main.py                      # uvicorn launcher
│   │   ├── http.py                      # FastAPI app (health checks, lifespan)
│   │   ├── bot.py                       # Discord client + token resolution
│   │   ├── config/
│   │   │   ├── settings.py              # Pydantic settings
│   │   │   ├── secrets.py               # GSM JSON-secret lookup
│   │   │   └── logging.py               # structlog setup
│   │   ├── cogs/
│   │   │   ├── diagnostics.py           # /ping, /info
│   │   │   └── valheim.py               # /valheim status|start|stop (scaffold)
│   │   ├── services/
│   │   │   ├── compute.py               # GCE instance start/stop/describe (Phase 3 stub)
│   │   │   └── server_query.py          # HTTP fetch of /status.json from VM daemon
│   │   └── utils/helpers.py
│   ├── tests/unit/                      # pytest, no integration tests yet
│   ├── Dockerfile                       # Multi-stage poetry → pip
│   └── pyproject.toml
│
├── infra/                               # Terraform — all GCP resources
│   ├── envs/prod/                       # Root module (state backend, var wiring)
│   └── modules/
│       ├── gcp-bootstrap/               # APIs, TF state bucket, WIF
│       ├── gcp-valheim-vm/              # VM, persistent disk, firewall, startup-script
│       ├── gcp-bot-runtime/             # Cloud Run service, AR repo, Cloud Build trigger, IAM
│       └── gcp-idle-watcher/            # Cloud Function + Scheduler that auto-stops idle VMs
│
├── server/                              # Files that run *inside* the Valheim VM
│   ├── docker-compose.yml               # lloesche/valheim-server-docker
│   ├── startup-script.sh.tftpl          # Per-boot bootstrap (idempotent)
│   └── scripts/                         # SSH-invoked helpers
│
├── docs/
│   ├── architecture.md                  # Diagram + interface boundaries
│   ├── bootstrap.md                     # One-time GCP setup
│   └── runbook.md                       # Recovery scenarios
│
├── .github/workflows/
│   ├── ci.yaml                          # Bot lint/test/build (path-filtered)
│   └── terraform.yml                    # TF fmt/validate/plan/apply (WIF-authed)
│
├── TODO.md                              # Manual setup checklist
└── CHANGELOG.md                         # Version history
```

See [docs/architecture.md](docs/architecture.md) for the network/data-flow diagram and the interface boundaries that make the cloud provider swappable.

---

## Tech stack

| Category | Technology |
|---|---|
| Runtime | Python 3.12 |
| Discord | discord.py 2.x (slash commands only) |
| HTTP | FastAPI + uvicorn (Cloud Run health checks) |
| Secrets | Google Secret Manager |
| Compute (Phase 3) | google-cloud-compute |
| Server query | httpx (HTTP fetch from log-scraping daemon) |
| Config | Pydantic Settings |
| Logging | structlog (JSON) |
| Testing | pytest, pytest-asyncio |
| Linting | Ruff, MyPy |
| CI/CD | GitHub Actions, Cloud Build |
| Infrastructure | Terraform, GCP (Cloud Run, GCE, GSM) |

---

## License

MIT — see [LICENSE](LICENSE).

---

## Documentation

- [docs/architecture.md](./docs/architecture.md) — Component diagram + interface boundaries
- [docs/bootstrap.md](./docs/bootstrap.md) — One-time GCP setup (state bucket, APIs, WIF)
- [docs/runbook.md](./docs/runbook.md) — Recovery procedures
- [infra/README.md](./infra/README.md) — Terraform layout
- [bot/README.md](./bot/README.md) — Bot-only quickstart
- [TODO.md](./TODO.md) — Manual setup checklist
- [CHANGELOG.md](./CHANGELOG.md) — Version history
