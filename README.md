# Mr. Swede

[![CI](https://github.com/jonlee-dev/mr-swede/actions/workflows/ci.yaml/badge.svg)](https://github.com/jonlee-dev/mr-swede/actions/workflows/ci.yaml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.3+-blue.svg)](https://discordpy.readthedocs.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A multi-feature Discord bot for our server. Today it runs an on-demand Valheim game server and a Lavalink-backed music player; the cog architecture is built so the next feature is just another cog and (if it needs cloud resources) another Terraform module. The bot and Lavalink co-tenant a single always-on GCE `e2-small` VM (as of 2026-05-12); Valheim runs on a separate on-demand GCE VM that only spins up when someone asks for it.

> **History.** v3.0.0 pruned the original Overwatch-stats and music features down to a Valheim-only scope ([CHANGELOG.md](./CHANGELOG.md)). v4.x reintroduced music as a Lavalink-on-GCE deployment with its own cog and idle-watcher target. v4.3 (2026-05-12) folded the bot off Cloud Run and onto a single VM with Lavalink — saving ~$35/mo and eliminating the music cold-start UX cost. See [docs/prd.md](./docs/prd.md) for the target architecture and decisions log.

---

## Repository layout

| Path | Contents |
|---|---|
| [`bot/`](bot/) | Python (discord.py) bot. Slash-only. Cogs: `diagnostics`, `valheim`, `music`. Runs as `bot.service` on the bot-vm; the VM clones this repo and runs from source. |
| [`infra/`](infra/) | Terraform for all GCP resources (bot+Lavalink VM, Valheim VM, idle watcher, retained Cloud Run rollback). |
| [`server/`](server/) | Files that run *inside* the GCE VMs — Valheim docker-compose + status daemon, Lavalink jar bootstrap + systemd units, bot systemd units + watchdog. |
| [`docs/`](docs/) | PRD, architecture diagram, bootstrap procedure, runbook. |

Bot-related Poetry / pytest / Docker commands run from inside [`bot/`](bot/). Terraform commands run from `infra/envs/prod/`.

---

## Status

The bot is fully functional. `/valheim *` is wired to GCE and a log-scraping HTTP daemon on the Valheim VM; `/music *` is wired to Lavalink (co-tenanted on the same VM as the bot) via Wavelink. The GCP infra is fully Terraform-managed:

- [`gcp-bootstrap`](infra/modules/gcp-bootstrap) — APIs, state bucket, Workload Identity Federation
- [`gcp-valheim-vm`](infra/modules/gcp-valheim-vm) — VPC (shared with bot-vm), firewall, persistent disk, Valheim VM, server-password GSM secret
- [`gcp-bot-vm`](infra/modules/gcp-bot-vm) — **current bot home (2026-05-12+)**. GCE `e2-small` co-tenanting `bot.service` + `lavalink.service`, IAP-SSH firewall, lavalink-server-password + spotify-client-credentials GSM bindings. Reuses the Valheim VPC and the `mr-swede-sa` service account from `gcp-bot-runtime`.
- [`gcp-bot-runtime`](infra/modules/gcp-bot-runtime) — Cloud Run service, Artifact Registry repo, Cloud Build trigger, IAM, Discord-secret container. Kept at `min=0, max=1` as a rollback option; will be retired after the bot-vm soak.
- [`gcp-lavalink-vm`](infra/modules/gcp-lavalink-vm) — *retired* standalone Lavalink VM. Module is kept short-term as a rollback option; `server/lavalink/` (which it consumed) is now also consumed by `gcp-bot-vm` for Lavalink config.
- [`gcp-idle-watcher`](infra/modules/gcp-idle-watcher) — Cloud Function + Scheduler. Single-target since 2026-05-12 (Valheim via `/status.json`); the Lavalink target was dropped because Lavalink is always-on. Multi-target shape retained via `count`-guarded resources for future use.

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
| `/music play <query>` | Joins your voice channel, plays a YouTube search/URL or a Spotify track / playlist / album URL. YouTube playlist URLs work too. Up to 100 tracks per URL. (Pre-2026-05-12 this also auto-started a standalone Lavalink VM; Lavalink is now co-tenanted with the bot at `localhost:2333`, so first-play of a session is instant.) |
| `/music skip` | Skip the current track |
| `/music pause` / `/music resume` | Toggle playback |
| `/music stop` | Stop playback, clear the queue, leave the voice channel |
| `/music queue` | Show the current queue |
| `/music nowplaying` | Show the current track + position |
| `/music volume <0-100>` | Set playback volume |
| `/music shuffle` | Shuffle the queue |
| `/music loop <off\|track\|queue>` | Toggle loop modes |

The `/music *` group is gated to a designated `MUSIC_COMMAND_CHANNEL_ID` (default: `#bot-spam`) but joins whichever voice channel the invoking user is in. Lavalink now runs continuously alongside the bot — no auto-stop — so there's no cold-start cost on the first `/music play` of a session.

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

The bot starts a FastAPI server on port 8080 (used by the watchdog's `/livez` probe in production; harmless locally) and connects to Discord in the background. If you don't have GSM access, set `DISCORD_TOKEN` in `bot/.env` to bypass GSM entirely.

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

Infra is Terraform-managed:

```bash
cd infra/envs/prod
terraform plan
terraform apply
```

The bot itself deploys manually from the VM (since 2026-05-12):

```bash
gcloud compute ssh bot-vm --tunnel-through-iap --zone us-central1-a --project mr-swede
cd /opt/mr-swede
sudo -u bot git pull
sudo -u bot bash -c 'cd bot && poetry install --no-interaction --no-root'   # only on dep changes
sudo systemctl restart bot
sudo journalctl -u bot -f
```

Cloud Build still pushes images to Artifact Registry on every master commit (the trigger is wired up via `gcp-bot-runtime`), but the deployed Cloud Run service is at `min=0` and doesn't pick them up. To roll back to Cloud Run: see [runbook §17](docs/runbook.md).

CI runs `fmt → validate → plan` on every PR touching `infra/**` and runs `apply` on merge to `master` (gated by the `prod` GitHub Environment). Auth is via Workload Identity Federation — no JSON keys.

**First-apply prerequisites and the `discord-bot-secrets` import step are documented in [TODO.md](./TODO.md#first-time-gcp-setup).** Reading that section before the first `terraform apply` is mandatory; the import has to happen between `plan` and `apply` or TF will try to create a duplicate of an already-existing GSM secret.

### Cost estimate

Numbers below are calibrated against 8 days of real usage data
(2026-05-02 → 2026-05-10) plus the 2026-05-12 bot+Lavalink co-tenancy
migration — see `docs/prd.md` decisions log for the breakdown.

| Component | ~Monthly | Notes |
|---|---|---|
| **Bot + Lavalink VM** (e2-small, always-on) | ~$10 | $0.014/hr × 730 hr. Co-tenants `bot.service` (Python + discord.py) and `lavalink.service` on a single VM. Replaced Cloud Run (~$13/mo at min=1) + standalone Lavalink VM (~$35/mo idle — see below). |
| Valheim VM (e2-standard-2, ~7 hr/day measured) | ~$14 | $0.067/hr. Idle-watcher stops after 90-120 min idle; saves ~$34/mo vs always-on ($48). |
| Persistent disks (30 GB boot + 20 GB data on Valheim, 15 GB boot on bot-vm) | ~$6.50 | pd-balanced at $0.10/GB-mo. Billed regardless of VM state. |
| Egress (Discord WSS heartbeats + voice UDP + Valheim game traffic) | ~$5-10 | Highly usage-dependent. Voice/game traffic dominates when actively played. |
| Idle watcher (Cloud Function + Scheduler) | <$0.05 | Under free tier — 30-min cron, <2s execution per tick. Now single-target (Valheim only). |
| Artifact Registry (bot image, unused since 2026-05-12) | ~$0.50 | ~5 GB stored. Cloud Build still pushes images on master commits but Cloud Run is at min=0; will be cleaned up after the bot-vm soak. |
| Cloud Run mr-swede (min=0, rollback option) | $0 | No traffic; only billed if you set min=1. |
| **Total at current usage** | **~$36-41/mo** | |

**Previous estimate** (~$72-77/mo, pre-2026-05-12): bot on Cloud Run
at min=1 ran ~$13/mo and standalone Lavalink VM was budgeted at
~$1.50/mo but in reality ran 24/7 at ~$35/mo because the idle-watcher
target was misconfigured for months. Folding bot+Lavalink onto a
single e2-small captures both savings.

**Optimization knobs** (none worth pulling at current scale):

- *Skip idle-watcher (Valheim)* → +$34/mo, no operator action required after a session, no cold-start UX cost. Not worth.
- *Roll bot back to Cloud Run* → +$3/mo, regain Cloud Build auto-deploy + Cloud Run's `/livez` kill-and-replace as a managed service. Watchdog timer on bot-vm replicates the kill-and-replace; manual `git pull + systemctl restart` replaces auto-deploy. Worth revisiting only if the manual deploy cadence becomes a pain point.

The big cost remaining is the Valheim VM (e2-standard-2, ~7hr/day actual usage); the idle-watcher already cuts that ~70%. The bot+Lavalink VM is cheap enough to keep always-on, which also kills the music cold-start UX cost.

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
| `DISCORD_SECRET_PATH` | auto-built from `GCP_PROJECT_ID` | Full GSM secret resource path (`projects/<num>/secrets/discord-bot-secrets/versions/latest`). bot-vm gets this from Terraform via `/etc/bot/bot.env`; locally the bot builds a default. |
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
├── bot/                                 # Python (discord.py); deployed via git clone to bot-vm
│   ├── src/
│   │   ├── main.py                      # uvicorn launcher
│   │   ├── http.py                      # FastAPI app (/livez liveness probe, lifespan)
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
│   ├── Dockerfile                       # Multi-stage poetry → pip (still built by Cloud Build for the Cloud Run rollback path)
│   └── pyproject.toml
│
├── infra/                               # Terraform — all GCP resources
│   ├── envs/prod/                       # Root module (state backend, var wiring)
│   └── modules/
│       ├── gcp-bootstrap/               # APIs, TF state bucket, WIF
│       ├── gcp-valheim-vm/              # Valheim VM, persistent disk, firewall, startup-script
│       ├── gcp-bot-vm/                  # Bot + Lavalink co-tenanted VM (current bot home)
│       ├── gcp-bot-runtime/             # Cloud Run service (legacy/rollback only), AR repo, Cloud Build trigger, IAM
│       ├── gcp-lavalink-vm/             # Standalone Lavalink VM (retired; rollback option)
│       └── gcp-idle-watcher/            # Cloud Function + Scheduler (Valheim only since 5/12)
│
├── server/                              # Files that run *inside* the GCE VMs
│   ├── docker-compose.yml               # Valheim: lloesche/valheim-server-docker
│   ├── startup-script.sh.tftpl          # Valheim per-boot bootstrap (idempotent)
│   ├── scripts/                         # Valheim helpers (status daemon, ssh-invoked ops)
│   ├── lavalink/                        # Lavalink jar bootstrap, application.yml, systemd units (read by gcp-bot-vm)
│   └── bot-vm/                          # bot-vm startup-script + bot.service / watchdog / fetch-secrets units
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
| Runtime | Python 3.11+ (3.11 on bot-vm via Debian 12 stock; 3.12 in CI) |
| Discord | discord.py[voice] 2.x (slash commands only; PyNaCl for voice) |
| Music | Lavalink 4.2.x (Java, co-tenanted on bot-vm) + Wavelink 3.5.x (Python client) + youtube-source plugin + lavasrc (Spotify URL resolution) |
| HTTP | FastAPI + uvicorn (powers the `/livez` liveness probe consumed by the bot-vm watchdog timer) |
| Secrets | Google Secret Manager |
| Compute | google-cloud-compute (start/stop/describe) |
| Server query | httpx (Valheim `/status.json`); urllib (idle-watcher probes) |
| Config | Pydantic Settings |
| Logging | structlog (JSON) |
| Testing | pytest, pytest-asyncio |
| Linting | Ruff, MyPy |
| CI/CD | GitHub Actions, Cloud Build |
| Infrastructure | Terraform, GCP (GCE bot-vm + Valheim VM, GSM, Cloud Functions, Cloud Scheduler; Cloud Run kept at min=0 as a rollback option) |

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
