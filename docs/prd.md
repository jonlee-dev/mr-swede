# Mr. Swede — Product Requirements & Architecture

**Status**: living document. Updated when significant decisions land.
**Last revised**: 2026-04-28
**Owners**: jonlee-dev

This document describes what Mr. Swede *is*, what it *does*, what it *will do*, and the architectural rules that keep adding features cheap. It is the source of truth when an existing doc and this PRD disagree.

---

## 1. Vision

Mr. Swede is a personal Discord bot that lets a small friend group **operate cloud-hosted services from inside Discord**. Today: an on-demand Valheim server. Soon: a Lavalink-backed music player. Later: any other "useful thing that lives in cloud" the maintainer wants behind a slash command.

Constraints that won't change:

- **Single maintainer**, hobby budget. Costs measured in tens of dollars, not hundreds. On-demand resources beat always-on resources.
- **Cloud Run for the bot**. Slash-only, gateway-based, `min_instances=1` (Discord drops idle gateway sessions), `cpu_idle=false` (background work doesn't count as request processing).
- **Terraform for everything cloud**. Manual `gcloud` is for one-off recovery, never for routine ops.
- **Pythonic, learning-oriented codebase**. Deep modules with narrow public surfaces. Tests live alongside the code that needs them.

---

## 2. What exists today (v3.2.0)

### Components

| Component | Where it lives | What it does |
|---|---|---|
| **Bot service** | [`bot/`](../bot/) — Cloud Run, Python 3.12 | Discord gateway client, FastAPI health endpoint. `min=1`, always-on CPU. |
| **Bot infra** | [`infra/modules/gcp-bot-runtime`](../infra/modules/gcp-bot-runtime) | Cloud Run service + Cloud Build trigger + IAM + Discord secret container |
| **Valheim VM** | [`infra/modules/gcp-valheim-vm`](../infra/modules/gcp-valheim-vm) + [`server/`](../server/) | GCE VM running `lloesche/valheim-server`. Boot via metadata.startup-script (NOT cloud-init — Debian default doesn't ship cloud-init). |
| **Idle watcher** | [`infra/modules/gcp-idle-watcher`](../infra/modules/gcp-idle-watcher) | Cloud Function + Scheduler that polls the VM's `/status.json` and stops it after N empty checks. |
| **Bootstrap** | [`infra/modules/gcp-bootstrap`](../infra/modules/gcp-bootstrap) | One-time TF state bucket + Workload Identity Federation + project APIs. |

### Discord surface

| Command | Behavior |
|---|---|
| `/ping` | Latency check |
| `/info` | Bot version + loaded cog list |
| `/valheim status` | Show VM state, PlayFab join code, server password, player count |
| `/valheim start` | Boot the Valheim VM (idempotent) |
| `/valheim stop` | Stop the Valheim VM (idempotent) |

### Key architectural patterns we already follow (and will keep)

1. **Cog per feature group** — `diagnostics.py`, `valheim.py`. Adding a feature means adding a cog, not extending an existing one.
2. **Service module per external system** — `services/compute.py` (GCE), `services/server_query.py` (HTTP fetch from VM daemon). Cogs orchestrate; services do I/O.
3. **Frozen dataclasses for cross-module values** — `InstanceState`, `LiveStatus`. Public surface is a dataclass, not a dict-with-implicit-keys.
4. **GSM-via-env-path** — secret resource paths are env vars (`DISCORD_SECRET_PATH`, `VALHEIM_PASSWORD_SECRET_PATH`). Bot SA gets secret-scoped IAM. No project-wide bindings.
5. **GCE control plane = custom role + instance-scoped binding** — `mrSwedeVmController` (`compute.instances.{get,start,stop}` + `zoneOperations.get`), bound at the instance level. Both bot SA and idle-watcher SA use it.
6. **Startup-script is idempotent** — runs every boot. First boot installs Docker; subsequent boots self-heal systemd units. Template files in [`server/`](../server/) are inlined as base64.
7. **Local quality gates mirror CI** — `make -C bot check` runs the same ruff + mypy + pytest + poetry-lock-check that GitHub Actions runs.

---

## 3. Target architecture (v4.0)

```
                     Discord (gateway WSS + voice UDP)
                              ▲
                              │
                ┌─────────────┴───────────────┐
                │   Bot — Cloud Run (Python)  │
                │   ┌───────────────────────┐ │
                │   │  Slash command tree   │ │
                │   │  • /ping /info        │ │
                │   │  • /valheim *         │ │
                │   │  • /music * (NEW)     │ │
                │   └─────┬────────────┬────┘ │
                │         │            │      │
                │   ┌─────▼─────┐ ┌────▼────┐ │
                │   │ services/ │ │  cogs/  │ │
                │   │  compute  │ │ valheim │ │
                │   │  query    │ │  music  │ │
                │   │  music    │ │ diag    │ │
                │   └─────┬─────┘ └─────────┘ │
                └─────────┼─────────────────────┘
                          │
        ┌─────────────────┼──────────────────────────┐
        │                 │                          │
   ┌────▼─────┐     ┌─────▼──────┐         ┌─────────▼──────────┐
   │ Valheim  │     │  Lavalink  │         │  Idle watcher      │
   │  VM      │     │   VM       │         │  (Cloud Function   │
   │          │     │   (NEW)    │         │   + Scheduler)     │
   │ GCE      │     │  GCE       │         │                    │
   │ on-demand│     │  on-demand │         │ polls both VMs     │
   └────▲─────┘     └────▲───────┘         │ stops idle ones    │
        │                │                  └────────────────────┘
        │ stop/start     │ stop/start
        └────────────────┴── via mrSwedeVmController custom role
                            (instance-scoped per VM)
```

### What's new in v4.0

- **Music cog** ([`bot/src/cogs/music.py`](../bot/src/cogs/music.py)) using **Wavelink** to drive Lavalink.
- **Lavalink VM** ([`infra/modules/gcp-lavalink-vm`](../infra/modules/gcp-lavalink-vm)) — GCE on-demand, mirrors `gcp-valheim-vm` shape.
- **Lavalink runtime artifacts** ([`server/lavalink/`](../server/lavalink/)) — docker-compose, fetch-secrets, systemd units. Same pattern as `server/`.
- **Idle watcher generalized** — currently keyed to one VM. Becomes a multi-target watcher (Valheim VM + Lavalink VM) with one state object per target.
- **Music service module** ([`bot/src/services/music.py`](../bot/src/services/music.py)) — Wavelink node lifecycle, voice state forwarding, queue helpers.

---

## 4. The music feature (the actual work for v4.0)

### What "controlled in #bot-spam" means

`/music *` slash commands work only when invoked from a channel matching `MUSIC_COMMAND_CHANNEL_ID` (env var, configurable). Invoked elsewhere → ephemeral "Use #bot-spam" reply, no I/O.

The bot does NOT consume `MESSAGE_CONTENT` intent. Slash-only — same hygiene as the rest of the bot.

### Commands

| Command | Behavior |
|---|---|
| `/music play <query>` | Search YouTube (or treat as URL); enqueue. If queue was empty, also start playback. Joins your voice channel if not already in one. |
| `/music skip` | Skip the currently playing track. |
| `/music pause` / `/music resume` | Toggle playback. |
| `/music stop` | Stop playback, clear the queue, leave voice. |
| `/music queue` | Show the next 10 queued tracks + currently playing. |
| `/music nowplaying` | Show current track + position/duration. |
| `/music volume <0-200>` | Set per-guild volume. Persists in-memory only; bot restart resets to 100. |
| `/music shuffle` | Shuffle queue (idempotent if queue ≤ 1). |
| `/music loop <off\|track\|queue>` | Loop mode. |

Out of scope for v4.0:
- `/music seek` — punt; nice-to-have, not core
- Spotify URLs — `lavasrc` plugin operational weight not justified for now
- Per-user playlists / saved queues
- Slash autocomplete on `/music play` — would require live YouTube search on every keystroke

### State ownership

| State | Owner | Lifetime |
|---|---|---|
| Queue (per guild) | Bot (in-memory, via `wavelink.Queue`) | Lost on bot restart |
| Currently playing track | Lavalink (authoritative) | Lost on Lavalink restart |
| Volume / loop mode (per guild) | Bot (in-memory) | Lost on bot restart |
| Voice channel binding | Discord (bot maintains via Wavelink) | Lost on bot or Lavalink restart |
| Lavalink VM lifecycle | GCE (TF-managed instance) | Persistent until stopped |

The bot is the orchestrator; Lavalink is the audio engine; Discord is the transport. None of these survive their own restart cleanly. **We intentionally don't persist queue state** — losing 3 songs of context on a deploy is acceptable for a hobby bot. Reintroducing persistence is a separate ticket if it ever bites.

### Lifecycle

```
User: /music play <song>
  │
  ▼
Bot cog: channel-scope check. If not #bot-spam → ephemeral redirect.
  │
  ▼
Bot cog: ensure Lavalink reachable.
  │
  ├── Lavalink VM is RUNNING + bot has WebSocket → continue
  │
  └── Lavalink VM is TERMINATED → call services/compute.start_instance()
      │     "Starting music server, ~30s..."
      ▼
      Wait until Lavalink WebSocket is open (with timeout)
  │
  ▼
Bot joins user's voice channel via Wavelink
  │
  ▼
Wavelink resolves <song> via youtube-source plugin → track
  │
  ▼
Bot: queue.put(track); if not playing, player.play(queue.get())
  │
  ▼
Lavalink streams audio frames directly to Discord (UDP)
  │
  ▼
On track end: bot's wavelink event handler pulls next from queue
  │
  ▼
Queue empty + voice channel empty → bot disconnects voice (after 5min idle)
                                  → idle watcher eventually stops Lavalink VM
```

### Failure modes (and what we do about each)

| Failure | Handling |
|---|---|
| Lavalink VM stopped, user runs `/music play` | Bot starts the VM, defers + waits up to 60s for WebSocket. If still not ready, ephemeral "Lavalink slow to start, try again in a minute." |
| User not in a voice channel | Ephemeral "Join a voice channel first, then re-run." |
| YouTube query returns nothing | Ephemeral "No results for that." |
| Track fails mid-play (404, region lock, etc.) | Skip silently to next track in queue; log warning. |
| Bot restarts mid-playback | Bot re-establishes Wavelink WebSocket; current track is lost (queue too); user must re-`/music play`. |
| Lavalink restarts mid-playback (rare — only on VM stop/start) | Same as above. |
| Idle watcher stops Lavalink VM during silence | Next `/music play` triggers VM start automatically. ~30s cold start. |

### Wavelink integration boundary

`bot/src/services/music.py` owns:
- `Wavelink.Pool` connection setup at bot startup (after Lavalink VM is reachable)
- Re-connection logic on Lavalink restart
- A typed `MusicPlayer` wrapper exposing only the operations the cog uses (so the cog doesn't import wavelink directly)
- A `TrackInfo` frozen dataclass that the cog consumes for embeds

Cog talks to `services/music.py`. Tests of `services/music.py` mock Wavelink at the library boundary (so we never actually open a Wavelink WebSocket in unit tests). Cog tests mock `services/music.py` entirely.

---

## 5. Cross-cutting infrastructure rules

These apply to every existing and future feature.

### 5.1 IAM scoping

- **No project-wide bindings on bot SA**. Every grant is either secret-scoped or instance-scoped.
- **Custom roles for narrow permission sets**. `mrSwedeVmController` is the model. If a future feature needs a different perm set (e.g., Cloud Storage object IO), make a new custom role rather than expanding `vm_controller`.
- **Service accounts per concern**. Bot SA, Valheim VM SA, idle-watcher SA, Lavalink VM SA. Each gets only what it needs.

### 5.2 Secret management

- **GSM secrets, env-var resource paths**. The bot never hardcodes a project number; it reads `<NAME>_SECRET_PATH` from env.
- **Out-of-band seeding**. TF creates the secret container; values are seeded with `gcloud secrets versions add`. Values never enter TF state.
- **Plain string OR JSON, both supported in `secrets.py`**. `_fetch_secret_string` and `_fetch_secret_json` are the two helpers.

### 5.3 Configuration

- **Pydantic Settings + env-var aliases**. New configurable behavior = new field on `Settings` with a clear `alias=` and description.
- **Sensible defaults baked in**. The bot starts with no `.env` if all defaults are acceptable.
- **`bot/env.example` is the living config doc**. Every Pydantic field has a corresponding entry there.

### 5.4 Logging + observability

- **`structlog` for everything**. JSON in production, console in dev. Every log event has key-value context.
- **Cloud Logging is the destination**. The runtime SA gets `roles/logging.logWriter` (project-scoped — only writes, never reads).
- **`/health` and `/metrics` are not load-bearing**. They expose `bot_ready`, guild count, latency. Cloud Run probes don't actually use them; they're for human smoke tests.
- **Future**: log-based alerts (Cloud Monitoring) when feature counts deviate. Out of scope for now.

### 5.5 Cost discipline

| Always-on | Why | Knob |
|---|---|---|
| Bot Cloud Run, min=1, cpu_idle=false | Discord drops idle gateway sessions; Discord interactions arrive over WS not over Cloud Run port | None — required |
| Idle-watcher GCS state bucket | <1KB; rounding error | None |

| On-demand (idle-watcher gates) | When it runs | Bill |
|---|---|---|
| Valheim VM | When `/valheim start`, until 60-90 min of empty A2S checks | ~$5-10/mo at 1-3hr/day usage |
| Lavalink VM (NEW) | When `/music play` triggers it, until 5 min empty queue + 5 min empty voice | ~$3-5/mo at 1hr/day usage |

The watcher is the cost-discipline mechanism. **Anything new that needs cloud compute should plug into the same idle-watcher pattern.**

### 5.6 Cutover discipline

- **TF-managed = checked into git**. Manual `gcloud run deploy` etc. for emergencies only.
- **Any non-TF cloud resource is technical debt**. Document it in TODO.md; remove or import into TF on the next pass.

---

## 6. Extensibility levers — what "deep modules + flexible levers" means here

Each lever below is a place where adding a future feature is cheap because the architecture made room.

### 6.1 Cogs are the unit of feature addition

Adding a new feature group → add a new file under `bot/src/cogs/`. The cog tuple in [`bot/src/bot.py`](../bot/src/bot.py) is the single registration point.

**Future shape (illustrative, not commitments)**:

```python
COG_MODULES = (
    "src.cogs.diagnostics",
    "src.cogs.valheim",
    "src.cogs.music",
    "src.cogs.minecraft",  # if we ever add a Minecraft server
    "src.cogs.scheduled",  # if we add /schedule announce / /schedule remind
    "src.cogs.audit",      # if we want /audit log of who did what
)
```

The cog's only responsibility: bind slash commands to handlers. No I/O. Any I/O delegates to `services/`.

### 6.2 `services/` is where I/O lives, one module per external system

```
services/
├── compute.py        # GCE control plane (Valheim, Lavalink, future game VMs)
├── server_query.py   # HTTP fetch from VM-side daemons
├── music.py          # Wavelink wrapper (NEW in v4)
├── ...
```

A new external system = a new module. Public surface is async functions returning frozen dataclasses. **Never expose raw client objects to cogs.** That's how we kept compute-as-three-functions cheap to swap to AWS.

### 6.3 Game-server abstraction (future, illustrative)

If we ever add a second game server (Minecraft, Palworld, etc.), the path of least resistance is:

```python
# bot/src/services/game_server.py — Protocol
class GameServer(Protocol):
    async def status(self) -> GameServerStatus: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...

# Implementations:
#   bot/src/services/games/valheim.py
#   bot/src/services/games/minecraft.py
#   bot/src/services/games/palworld.py
```

Cogs would dispatch to a registry keyed by name. **We don't build this in v4.0** — single concrete impl is cheaper today, per the same logic that kept `compute.py` GCE-only. We define the Protocol the day we add the second game.

### 6.4 Scheduled tasks (future, illustrative)

If we ever want `/schedule announce "raid at 8pm"` or recurring events:

- Cloud Scheduler hits a new HTTP endpoint on the bot
- Bot's FastAPI handler dispatches to a registered set of "scheduled actions"
- Or: a new Cloud Function in `infra/modules/gcp-scheduler` (mirroring idle-watcher) handles whatever the action is

**We don't build this in v4.0**. The hook for it is "the bot's FastAPI app accepts new endpoint registrations from cogs," which we can add the day we need it.

### 6.5 Idle-watcher generalization (this we're doing in v4.0)

Currently the watcher is hard-coded to `valheim-server`. To support Lavalink too:

- Watcher reads a list of `(instance_name, zone, status_endpoint, empty_checks_to_stop)` targets from env
- State bucket holds one JSON object per target (`state-valheim-server.json`, `state-lavalink-server.json`)
- Same scheduler, same function, multiple iterations per tick

This is genuinely a deep-module change: same external interface (Cloud Scheduler hits the function), more capability inside.

### 6.6 Configurable knobs already in place

Every behavior worth toggling has an env var in [`bot/src/config/settings.py`](../bot/src/config/settings.py):

- `DISCORD_GUILD_ID` — instant-sync guild for dev
- `VALHEIM_INSTANCE_NAME`, `VALHEIM_ZONE` — VM target
- `VALHEIM_STATUS_HTTP_PORT` — daemon port
- (NEW) `MUSIC_COMMAND_CHANNEL_ID`, `LAVALINK_HOST`, `LAVALINK_PORT`, `LAVALINK_SECRET_PATH`

**Rule**: every env var must have a Pydantic field, a default, and an entry in `bot/env.example`. No env-var-as-magic-string usage outside `settings.py`.

---

## 7. Future work, in priority order

| Priority | Work item | Notes |
|---|---|---|
| ⏳ Next | **Music feature (v4.0)** | This PRD's main subject |
| 📋 Backlog | **Valheim mod support** | BEPINEX or ValheimPlus loader; mod files via GCS bucket; bot command to apply pending mods at next restart. Design open. |
| 📋 Backlog | **Stop+reboot persistence validation** | Stress-test that the data disk survives stop/start cycles correctly across all the things that can boot the VM. |
| 📋 Backlog | **Load testing** | What's the e2-standard-2 ceiling? Does world building under multi-player load degrade? Ticket: pick a stress profile, run for 30 min, decide if we bump to e2-standard-4. |
| 🔮 Future | **Game server abstraction** | Trigger: a second game server lands. Refactor `compute.py` into `Protocol`+impl per §6.3. |
| 🔮 Future | **Scheduled tasks** | Trigger: real user need for recurring announcements / reminders. §6.4 sketch. |
| 🔮 Future | **VPC Flow Logs / Ops Agent on VMs** | When the next "I can't connect" debug session would have benefited from packet-level logs. ~$0.50/mo. |

---

## 8. Open questions / decisions log

Decisions captured here so future-us doesn't re-litigate.

| Date | Decision | Why |
|---|---|---|
| 2026-04-26 | **Slash commands only, no MESSAGE_CONTENT intent** | Performance + hygiene. Privileged intents are an audit cost. |
| 2026-04-26 | **`min=max=1` Cloud Run, `cpu_idle=false`** | Discord gateway is single-process; bot does work outside HTTP requests |
| 2026-04-27 | **Log-scraping daemon, not Steam A2S, for Valheim status** | A2S broken under crossplay/PlayFab |
| 2026-04-27 | **Single concrete `compute.py`, not Protocol+impl** | Premature abstraction; flip when we add a second cloud or second game |
| 2026-04-28 | **Lavalink as a separate on-demand GCE VM** | Mirrors Valheim pattern; cheapest steady-state; matches existing TF patterns |
| 2026-04-28 | **Wavelink as Lavalink Python client** | Most discord.py-aligned, smaller surface, active maintenance |
| 2026-04-28 | **Lose music queue on bot restart** | Hobby tolerance for context loss; reintroduce persistence only if it bites |
| 2026-04-28 | **`/music *` only in #bot-spam** | Channel-scoped via env var, ephemeral redirect elsewhere |

---

## 9. Reading list — when joining this codebase

1. This PRD (you are here)
2. [`docs/architecture.md`](architecture.md) — diagrams + interface boundaries
3. [`docs/runbook.md`](runbook.md) — what to do when something is wedged
4. [`docs/bootstrap.md`](bootstrap.md) — one-time GCP setup
5. [`bot/README.md`](../bot/README.md) — bot-only quickstart
6. [`server/README.md`](../server/README.md) — what runs inside the Valheim VM
7. [`infra/README.md`](../infra/README.md) — TF layout
8. [`CHANGELOG.md`](../CHANGELOG.md) — what changed when, and why

When making a non-trivial change, update this PRD first. Code follows.
