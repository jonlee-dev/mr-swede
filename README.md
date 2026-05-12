# Mr. Swede

[![CI](https://github.com/jonlee-dev/mr-swede/actions/workflows/ci.yaml/badge.svg)](https://github.com/jonlee-dev/mr-swede/actions/workflows/ci.yaml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.3+-blue.svg)](https://discordpy.readthedocs.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A multi-feature Discord bot for our server. Today it runs an on-demand Valheim game server and a Lavalink-backed music player; the cog architecture is built so the next feature is just another cog and (if it needs cloud resources) another Terraform module. Bot runs on Cloud Run; both game/audio servers run on on-demand GCE VMs that only spin up when someone asks for them.

> **History.** v3.0.0 pruned the original Overwatch-stats and music features down to a Valheim-only scope ([CHANGELOG.md](./CHANGELOG.md)). v4.x reintroduces music as a Lavalink-on-GCE deployment with its own cog and idle-watcher target — see [docs/prd.md](./docs/prd.md) for the target architecture and the extensibility levers that should keep the next feature additive.

---

## Repository layout

| Path | Contents |
|---|---|
| [`bot/`](bot/) | Python (discord.py) bot — Cloud Run service. Slash-only. Cogs: `diagnostics`, `valheim`, `music`. |
| [`infra/`](infra/) | Terraform for all GCP resources (bot runtime, Valheim VM, Lavalink VM, idle watcher). |
| [`server/`](server/) | Files that run *inside* the GCE VMs — Valheim docker-compose + status daemon, Lavalink jar bootstrap + systemd unit. |
| [`docs/`](docs/) | PRD, architecture diagram, bootstrap procedure, runbook. |

Bot-related Poetry / pytest / Docker commands run from inside [`bot/`](bot/). Terraform commands run from `infra/envs/prod/`.

---

## Status

The bot is fully functional. `/valheim *` is wired to GCE and a log-scraping HTTP daemon on the Valheim VM; `/music *` is wired to a Lavalink server on a second GCE VM via Wavelink. The GCP infra is fully Terraform-managed across five modules:

- [`gcp-bootstrap`](infra/modules/gcp-bootstrap) — APIs, state bucket, Workload Identity Federation
- [`gcp-valheim-vm`](infra/modules/gcp-valheim-vm) — VPC (shared with Lavalink), firewall, persistent disk, Valheim VM, server-password GSM secret
- [`gcp-lavalink-vm`](infra/modules/gcp-lavalink-vm) — Lavalink VM (e2-small, no persistent disk), firewall, server-password GSM secret. Reuses the Valheim VPC.
- [`gcp-bot-runtime`](infra/modules/gcp-bot-runtime) — Cloud Run service, Artifact Registry repo, Cloud Build trigger, IAM, Discord-secret container, instance-scoped controller role bound to both VMs
- [`gcp-idle-watcher`](infra/modules/gcp-idle-watcher) — Cloud Function + Scheduler that polls each on-demand VM and stops it after N consecutive empty checks (default: ~60-90 min idle window per target). Multi-target: Valheim via `/status.json`, Lavalink via `/v4/players`.

See [TODO.md](./TODO.md) for the cutover checklist and the manual prerequisites (Discord developer portal, Cloud Build ↔ GitHub OAuth).

---

## Commands

| Command | Description |
|---|---|
| `/ping` | Latency check |
| `/info` | Bot version + per-feature command list |
| `/valheim status` | Show VM state, PlayFab join code, server password, and player count |
| `/valheim start` | Start the Valheim VM (idempotent) |
| `/valheim stop` | Stop the Valheim VM (idempotent) |
| `/music play <query>` | Auto-starts the Lavalink VM (idempotent), joins your voice channel, plays a YouTube search/URL or a Spotify track / playlist / album URL. YouTube playlist URLs work too. Up to 100 tracks per URL. |
| `/music skip` | Skip the current track |
| `/music pause` / `/music resume` | Toggle playback |
| `/music stop` | Stop playback, clear the queue, leave the voice channel |
| `/music queue` | Show the current queue |
| `/music nowplaying` | Show the current track + position |
| `/music volume <0-100>` | Set playback volume |
| `/music shuffle` | Shuffle the queue |
| `/music loop <off\|track\|queue>` | Toggle loop modes |

The `/music *` group is gated to a designated `MUSIC_COMMAND_CHANNEL_ID` (default: `#bot-spam`) but joins whichever voice channel the invoking user is in. The Lavalink VM is auto-stopped by the idle watcher ~60-90 min after the last player leaves, mirroring the Valheim auto-stop behavior.

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

Numbers below are calibrated against 8 days of real usage data
(2026-05-02 → 2026-05-10) — see `docs/prd.md` decisions log entry
2026-05-10 for the full breakdown.

| Component | ~Monthly | Notes |
|---|---|---|
| Cloud Run bot (`min=max=1`, `cpu_idle=false`, 1 vCPU + 512 Mi) | ~$45 | Always-on CPU is mandatory: Discord gateway is a long-lived WebSocket, not request/response. us-central1 Tier 1 rate × 2.59M vCPU-s/mo minus free tier. |
| Valheim VM (e2-standard-2, ~7 hr/day measured) | ~$14 | $0.067/hr. Idle-watcher stops after 90-120 min idle; saves ~$34/mo vs always-on ($48). |
| Lavalink VM (e2-small, ~3.6 hr/day measured) | ~$1.50 | $0.014/hr. Idle-watcher saves ~$8.50/mo vs always-on ($10). |
| Persistent disks (30 GB boot + 20 GB data on Valheim, 10 GB boot on Lavalink) | ~$6 | pd-balanced at $0.10/GB-mo. Billed regardless of VM state. |
| Egress (Discord WSS heartbeats + voice UDP + Valheim game traffic) | ~$5-10 | Highly usage-dependent. Voice/game traffic dominates when actively played. |
| Idle watcher (Cloud Function + Scheduler) | <$0.05 | Under free tier — 30-min cron, <2s execution per tick. |
| Artifact Registry (bot image) | ~$0.50 | ~5 GB stored |
| **Total at current usage** | **~$72-77/mo** | |

**Optimization knobs** (none worth pulling at current scale):

- *Skip idle-watcher* → +$40/mo, no operator action required after a session, no cold-start UX cost. Not worth.
- *Move bot off Cloud Run to e2-small VM* → -$35/mo, lose Cloud Build auto-deploy + Cloud Run's `/livez` kill-and-replace + zero-ops managed restart. Would need to rebuild equivalents in systemd. Worth revisiting only if cost becomes an actual pain point.

The two big costs are the bot's always-on CPU and the on-demand VMs running 24/7. The idle watcher cuts each VM bill by ~70-80% in practice — it stops a VM after 60-90 minutes of zero players, so the cost table assumes ~3 hours of daily usage per VM rather than 24/7. The bot uses `cpu_idle = false` because Discord delivers slash commands over a WebSocket gateway (not over Cloud Run's HTTP port) — a CPU-throttled service starves the worker thread doing TLS handshakes from `/valheim *` and `/music *` calls. See [`infra/modules/gcp-bot-runtime/service.tf`](infra/modules/gcp-bot-runtime/service.tf) for the full reasoning.

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
| `VALHEIM_INSTANCE_NAME` | `valheim-server` | Valheim instance name to control |
| `LAVALINK_ZONE` | `us-central1-a` | Compute zone of the Lavalink VM |
| `LAVALINK_INSTANCE_NAME` | `lavalink-server` | Lavalink instance name to control |
| `LAVALINK_HOST` | `""` (auto-resolve) | Lavalink host. Empty = resolve the VM's public IP at runtime; set explicitly for local dev. |
| `LAVALINK_PORT` | `2333` | Lavalink REST/WS port |
| `MUSIC_COMMAND_CHANNEL_ID` | — | Discord channel ID where `/music *` is allowed (default: `#bot-spam`) |
| `DISCORD_SECRET_PATH` | auto-built from `GCP_PROJECT_ID` | Full GSM secret resource path (`projects/<num>/secrets/discord-bot-secrets/versions/latest`). Cloud Run gets this from Terraform; locally the bot builds a default. |
| `HOST` | `0.0.0.0` | HTTP server bind address |
| `PORT` | `8080` | HTTP server port |

### Local-dev overrides (skip GSM)

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Discord bot token — when set, GSM lookup is skipped |
| `DISCORD_APPLICATION_ID` | Discord application ID |
| `LAVALINK_PASSWORD` | Lavalink password — when set, GSM lookup of `lavalink-server-password` is skipped |

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

The nested-object form above is preferred. The bot also accepts dot-notation keys (`"mr-swede.token": "..."`) for backwards compatibility — see [bot/src/config/secrets.py](bot/src/config/secrets.py) for both lookup paths. The Valheim server password and the Lavalink server password live in separate single-string secrets (`valheim-server-password`, `lavalink-server-password`), each seeded out-of-band — see [docs/bootstrap.md](docs/bootstrap.md).

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
│   │   │   ├── settings.py              # Pydantic settings (incl. Lavalink + music channel)
│   │   │   ├── secrets.py               # GSM JSON-secret + Valheim/Lavalink password lookup
│   │   │   └── logging.py               # structlog setup
│   │   ├── cogs/
│   │   │   ├── diagnostics.py           # /ping, /info
│   │   │   ├── valheim.py               # /valheim status|start|stop
│   │   │   └── music.py                 # /music play|skip|pause|resume|stop|queue|...
│   │   ├── services/
│   │   │   ├── compute.py               # GCE instance start/stop/describe
│   │   │   ├── server_query.py          # HTTP fetch of /status.json from Valheim VM daemon
│   │   │   └── music.py                 # Wavelink wrapper (node connect, search, play)
│   │   └── utils/
│   │       ├── checks.py                # @requires_channel decorator (gates /music)
│   │       └── helpers.py
│   ├── tests/unit/                      # pytest, no integration tests yet
│   ├── Dockerfile                       # Multi-stage poetry → pip
│   └── pyproject.toml
│
├── infra/                               # Terraform — all GCP resources
│   ├── envs/prod/                       # Root module (state backend, var wiring)
│   └── modules/
│       ├── gcp-bootstrap/               # APIs, TF state bucket, WIF
│       ├── gcp-valheim-vm/              # Valheim VM, persistent disk, firewall, startup-script
│       ├── gcp-lavalink-vm/             # Lavalink VM (e2-small, no PD), firewall, password secret
│       ├── gcp-bot-runtime/             # Cloud Run service, AR repo, Cloud Build trigger, IAM
│       └── gcp-idle-watcher/            # Multi-target Cloud Function + Scheduler (Valheim + Lavalink)
│
├── server/                              # Files that run *inside* the GCE VMs
│   ├── docker-compose.yml               # Valheim: lloesche/valheim-server-docker
│   ├── startup-script.sh.tftpl          # Valheim per-boot bootstrap (idempotent)
│   ├── scripts/                         # Valheim helpers (status daemon, ssh-invoked ops)
│   └── lavalink/                        # Lavalink jar bootstrap, application.yml, systemd unit
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
| Discord | discord.py[voice] 2.x (slash commands only; PyNaCl for voice) |
| Music | Lavalink 4.2.x (Java, on GCE) + Wavelink 3.5.x (Python client) + youtube-source plugin + lavasrc (Spotify URL resolution) |
| HTTP | FastAPI + uvicorn (Cloud Run health checks) |
| Secrets | Google Secret Manager |
| Compute | google-cloud-compute (start/stop/describe) |
| Server query | httpx (Valheim `/status.json`); urllib (idle-watcher probes) |
| Config | Pydantic Settings |
| Logging | structlog (JSON) |
| Testing | pytest, pytest-asyncio |
| Linting | Ruff, MyPy |
| CI/CD | GitHub Actions, Cloud Build |
| Infrastructure | Terraform, GCP (Cloud Run, GCE, GSM, Cloud Functions, Cloud Scheduler) |

---

## License

MIT — see [LICENSE](LICENSE).

---

## Documentation

- **[docs/prd.md](./docs/prd.md)** — Product requirements + target architecture. Read this first when joining the repo or before a non-trivial change.
- [docs/architecture.md](./docs/architecture.md) — Component diagram + interface boundaries
- [docs/bootstrap.md](./docs/bootstrap.md) — One-time GCP setup (state bucket, APIs, WIF)
- [docs/runbook.md](./docs/runbook.md) — Recovery procedures
- [infra/README.md](./infra/README.md) — Terraform layout
- [bot/README.md](./bot/README.md) — Bot-only quickstart
- [TODO.md](./TODO.md) — Manual setup checklist
- [CHANGELOG.md](./CHANGELOG.md) — Version history
