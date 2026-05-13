# Mr. Swede — Product Requirements & Architecture

**Status**: living document. Updated when significant decisions land.
**Last revised**: 2026-05-10
**Owners**: jonlee-dev

This document describes what Mr. Swede *is*, what it *does*, what it *will do*, and the architectural rules that keep adding features cheap. It is the source of truth when an existing doc and this PRD disagree.

---

## 1. Vision

Mr. Swede is a personal Discord bot that lets a small friend group **operate cloud-hosted services from inside Discord**. Today: an on-demand Valheim server, a Lavalink-backed music player. Tomorrow: Spotify URL/playlist support. Later: any other "useful thing that lives in cloud" the maintainer wants behind a slash command.

Constraints that won't change:

- **Single maintainer**, hobby budget. Costs measured in tens of dollars, not hundreds. On-demand resources beat always-on resources.
- **Cloud Run for the bot**. Slash-only, gateway-based, `min_instances=1` (Discord drops idle gateway sessions), `cpu_idle=false` (background work doesn't count as request processing).
- **Terraform for everything cloud**. Manual `gcloud` is for one-off recovery, never for routine ops.
- **Pythonic, learning-oriented codebase**. Deep modules with narrow public surfaces. Tests live alongside the code that needs them.

---

## 2. What exists today (v4.x)

v4.0 shipped: music feature is live. v4.x is the current line with stability hardening on top.

### Components

| Component | Where it lives | What it does |
|---|---|---|
| **Bot + Lavalink VM** | [`infra/modules/gcp-bot-vm`](../infra/modules/gcp-bot-vm) + [`server/bot-vm/`](../server/bot-vm/) + [`bot/`](../bot/) | GCE `e2-small`, always-on. Co-tenants `bot.service` (Python 3.11, discord.py[voice], Discord gateway) and `lavalink.service` (Lavalink jar). Bot connects to Lavalink at `localhost:2333`. Boot via metadata.startup-script. Reuses the Valheim VPC. |
| **Bot runtime — legacy** | [`infra/modules/gcp-bot-runtime`](../infra/modules/gcp-bot-runtime) | Cloud Run service `mr-swede` + Cloud Build trigger + IAM + Discord secret container. Was the bot's home until 2026-05-12; now scaled to `min=0, max=1` as a one-flip rollback option. Service account `mr-swede-sa` is reused by `gcp-bot-vm`. |
| **Valheim VM** | [`infra/modules/gcp-valheim-vm`](../infra/modules/gcp-valheim-vm) + [`server/`](../server/) | GCE VM running `lloesche/valheim-server`. Boot via metadata.startup-script (NOT cloud-init — Debian default doesn't ship cloud-init). |
| **Lavalink VM — retired** | [`infra/modules/gcp-lavalink-vm`](../infra/modules/gcp-lavalink-vm) + [`server/lavalink/`](../server/lavalink/) | Was a standalone GCE `e2-small` running Lavalink. Folded into the bot VM on 2026-05-12; module kept short-term as a rollback option and as the source-of-truth for Lavalink config (which `gcp-bot-vm` reads from `server/lavalink/`). To be destroyed after the bot-vm soak. |
| **Idle watcher** | [`infra/modules/gcp-idle-watcher`](../infra/modules/gcp-idle-watcher) | Cloud Function + Scheduler. Now single-target (Valheim only); the Lavalink target was dropped 2026-05-12. Multi-target shape retained via `count`-guarded resources so a second target can be re-added with a one-line variable flip. |
| **Bootstrap** | [`infra/modules/gcp-bootstrap`](../infra/modules/gcp-bootstrap) | One-time TF state bucket + Workload Identity Federation + project APIs. |

### Discord surface

| Command | Behavior |
|---|---|
| `/ping` | Latency check |
| `/info` | Bot version + per-feature command list |
| `/valheim status` | Show VM state, PlayFab join code, server password, player count |
| `/valheim start` | Boot the Valheim VM (idempotent) |
| `/valheim stop` | Stop the Valheim VM (idempotent) |
| `/music play <query>` | Join your VC, enqueue and play the resolved track. (Pre-2026-05-12 this also auto-started a standalone Lavalink VM; Lavalink is now co-tenanted on the always-on bot VM at `localhost:2333` so first-play is instant.) |
| `/music skip` | Skip the current track |
| `/music pause` / `/music resume` | Toggle playback |
| `/music stop` | Stop, clear queue, leave voice |
| `/music queue` / `/music nowplaying` | Inspect playback state |
| `/music volume <0-100>` / `/music shuffle` / `/music loop <off\|track\|queue>` | Tune playback |

`/music *` is gated to a configured channel (`MUSIC_COMMAND_CHANNEL_ID`). The bot joins whichever voice channel the invoking user is currently in.

### Key architectural patterns we already follow (and will keep)

1. **Cog per feature group** — `diagnostics.py`, `valheim.py`, `music.py`. Adding a feature means adding a cog, not extending an existing one.
2. **Service module per external system** — `services/compute.py` (GCE), `services/server_query.py` (Valheim daemon), `services/music.py` (Wavelink → Lavalink). Cogs orchestrate; services do I/O.
3. **Frozen dataclasses for cross-module values** — `InstanceState`, `LiveStatus`. Public surface is a dataclass, not a dict-with-implicit-keys.
4. **GSM-via-env-path** — secret resource paths are env vars (`DISCORD_SECRET_PATH`, `VALHEIM_PASSWORD_SECRET_PATH`, `LAVALINK_PASSWORD_SECRET_PATH`). Bot SA gets secret-scoped IAM. No project-wide bindings.
5. **GCE control plane = custom role + instance-scoped binding** — `mrSwedeVmController` (`compute.instances.{get,start,stop}` + `zoneOperations.get`), bound per-instance. Bot SA and idle-watcher SA both use it, both VMs.
6. **Startup-script is idempotent** — runs every boot. Template files in [`server/`](../server/) and [`server/lavalink/`](../server/lavalink/) are inlined as base64.
7. **Channel-scoped command gating via `app_commands.check`** — `requires_channel("<settings_attr>")` decorator factory in [`bot/src/utils/checks.py`](../bot/src/utils/checks.py). Pure-logic predicate, fully unit-tested.
8. **Local quality gates mirror CI** — `make -C bot check` runs the same ruff + mypy + pytest + poetry-lock-check that GitHub Actions runs.

---

## 3. Architecture (current; v4.x — post-2026-05-12 co-tenant)

```
                     Discord (gateway WSS + voice UDP)
                              ▲
                              │
        ┌─────────────────────┴────────────────────────────┐
        │   Bot + Lavalink VM (gcp-bot-vm, e2-small)        │
        │                                                   │
        │   ┌───────────────────────────────────────────┐   │
        │   │  bot.service (Python 3.11)                │   │
        │   │   • Slash command tree                    │   │
        │   │     - /ping /info                         │   │
        │   │     - /valheim *                          │   │
        │   │     - /music *                            │   │
        │   │   • services/ + cogs/                     │   │
        │   │   • bot-watchdog.timer (kill+replace via  │   │
        │   │     systemd on 5x /livez 503)             │   │
        │   └──────────────┬───────────┬────────────────┘   │
        │                  │ localhost │                    │
        │                  │  :2333    │ instances.start    │
        │   ┌──────────────▼────────┐  │                    │
        │   │  lavalink.service     │  │                    │
        │   │  (Lavalink jar 4.2.2) │  │                    │
        │   └───────────────────────┘  │                    │
        └──────────────────────────────┼────────────────────┘
                                       │
                       ┌───────────────┼──────────────────┐
                       │               │                  │
                  ┌────▼─────┐    ┌────▼──────────────┐   │
                  │ Valheim  │    │  Idle watcher     │   │
                  │  VM      │    │  (Cloud Function  │   │
                  │          │    │   + Scheduler)    │   │
                  │ GCE      │    │                   │   │
                  │ on-demand│    │ polls Valheim VM  │   │
                  └────▲─────┘    │ stops if empty    │   │
                       │          │ (Lavalink target  │   │
                       │ stop/    │  dropped 5/12)    │   │
                       │ start    └───────────────────┘   │
                       └── via mrSwedeVmController custom │
                           role (instance-scoped)         │
                                                          │
   ┌─────────────────────────────────────────────────────┐│
   │  Cloud Run mr-swede (legacy, min=0)                 │◄┘
   │  Kept as a one-flip rollback (set min=1 to restore) │
   │  Does not serve traffic; will be destroyed after    │
   │  the bot-vm soak (~1 week from 2026-05-12).         │
   └─────────────────────────────────────────────────────┘
```

### What shipped in v4.0

- **Music cog** ([`bot/src/cogs/music.py`](../bot/src/cogs/music.py)) using **Wavelink 3.5.x** to drive Lavalink.
- **Lavalink VM** ([`infra/modules/gcp-lavalink-vm`](../infra/modules/gcp-lavalink-vm)) — GCE `e2-small`, on-demand, jar under systemd (no Docker — the Lavalink Docker images had broken bundled JDKs at the time).
- **Lavalink runtime artifacts** ([`server/lavalink/`](../server/lavalink/)) — `application.yml`, `fetch-secrets.sh`, two systemd units. Mirrors `server/` shape.
- **Idle watcher generalized** — multi-target now. Iterates `[(valheim, /status.json), (lavalink, /v4/players)]`. State is `state-<target>.json` in the same bucket.
- **Music service module** ([`bot/src/services/music.py`](../bot/src/services/music.py)) — idempotent node connect, search, play, queue helpers.
- **Channel gating** ([`bot/src/utils/checks.py`](../bot/src/utils/checks.py)) — `@requires_channel(...)` decorator factory. Used by `/music *`; reusable for any future channel-scoped feature.

---

## 4. The music feature (shipped — kept for context)

### What "controlled in #bot-spam" means

`/music *` slash commands work only when invoked from a channel matching `MUSIC_COMMAND_CHANNEL_ID` (env var, configurable). Invoked elsewhere → ephemeral "Use #bot-spam" reply, no I/O.

The bot does NOT consume `MESSAGE_CONTENT` intent. Slash-only — same hygiene as the rest of the bot.

The bot DOES consume the `voice_states` intent (required for joining VC), but only the bare minimum.

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

Out of scope for v4.0 (some now planned for v4.1+ — see §7):
- `/music seek` — punt; nice-to-have, not core
- ~~Spotify URLs~~ — **promoted** to next major work item; see §7 + §9.
- ~~Playlist support (URL-resolved)~~ — **promoted**; comes free with the lavasrc plugin once it lands.
- Per-user *saved* playlists (a library) — still out of scope; would require persistent state.
- Slash autocomplete on `/music play` — would require live search on every keystroke.

### State ownership

| State | Owner | Lifetime |
|---|---|---|
| Queue (per guild) | Bot (in-memory, via `wavelink.Queue`) | Lost on bot restart |
| Currently playing track | Lavalink (authoritative) | Lost on Lavalink restart |
| Volume / loop mode (per guild) | Bot (in-memory) | Lost on bot restart |
| Voice channel binding | Discord (bot maintains via Wavelink) | Lost on bot or Lavalink restart |
| Lavalink VM lifecycle | bot-vm systemd (always-on) | Persistent — Lavalink runs continuously alongside the bot |

The bot is the orchestrator; Lavalink is the audio engine; Discord is the transport. None of these survive their own restart cleanly. **We intentionally don't persist queue state** — losing 3 songs of context on a deploy is acceptable for a hobby bot. Reintroducing persistence is a separate ticket if it ever bites.

### Lifecycle (post-2026-05-12)

```
User: /music play <song>
  │
  ▼
Bot cog: channel-scope check. If not #bot-spam → ephemeral redirect.
  │
  ▼
Bot cog: ensure Lavalink reachable.
  │   (LAVALINK_HOST=localhost → "ensure VM running" path is skipped.
  │    Lavalink is co-tenanted on this same VM and is always up. The
  │    on-demand-start code path still exists for the standalone-VM
  │    rollback case; the cog branches on the env-var.)
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
Queue empty + voice channel empty → bot disconnects voice (after 5min idle).
                                  No Lavalink stop happens; Lavalink stays up
                                  for the next /music play.
```

### Failure modes (and what we do about each)

| Failure | Handling |
|---|---|
| User not in a voice channel | Ephemeral "Join a voice channel first, then re-run." |
| YouTube query returns nothing | Ephemeral "No results for that." |
| Track fails mid-play (404, region lock, etc.) | Skip silently to next track in queue; log warning. |
| Bot restarts mid-playback | Bot re-establishes Wavelink WebSocket; current track is lost (queue too); user must re-`/music play`. Watchdog (`bot-watchdog.timer`) restarts the bot on 5 consecutive `/livez` 503s — equivalent of Cloud Run's kill-and-replace. |
| Lavalink restarts mid-playback (rare — only on `systemctl restart lavalink` or VM reboot) | Same as above. With Lavalink co-tenanted on the same VM, this is now only triggered by config changes (`terraform apply` re-rendering `application.yml`) or the bot-vm reboots; idle-watcher-induced restarts are gone. |
| Lavalink endpoint changes (used to: VM stop/start → new public IP, stale node session) | **No longer applies** post-co-tenancy. The bot points at `localhost:2333`, which doesn't change. The stale-session handling in `services/music.py` (`_drop_stale_node` + `/v4/info` health check) is retained for the rollback-to-standalone-VM case. |

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

### 6.5 Idle-watcher generalization (shipped in v4.0)

Done. The watcher iterates over a `TARGETS` list in [`infra/modules/gcp-idle-watcher/function/main.py`](../infra/modules/gcp-idle-watcher/function/main.py):

```python
TARGETS = [
    ("valheim",  VALHEIM_ZONE,  VALHEIM_INSTANCE,  _probe_valheim),
    ("lavalink", LAVALINK_ZONE, LAVALINK_INSTANCE, _probe_lavalink),
]
```

- Each target has its own probe function (Valheim: `/status.json`; Lavalink: authenticated `/v4/players`).
- Each target has its own GCS state object (`state-valheim.json`, `state-lavalink.json`).
- One Cloud Function, one scheduler, one tick — multiple iterations per tick.
- Adding a third target = appending to `TARGETS` + adding a probe function. No new infra.

Probe failures are conservatively treated as "unknown" (do NOT increment the counter), so a transient outage of one target's daemon won't cause a false stop.

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
| ✅ Done | **Music feature (v4.0)** | Shipped — see §4. Wavelink 3.5 + Lavalink 4.2.2 + on-demand GCE. |
| ⚙️ In flight | **Stale-session hardening (v4.1)** | `_ensure_node_connected` adds a `/v4/info` health check that detects a stale Wavelink session (left over after a Lavalink VM stop/start cycle) and forces a fresh `Pool.connect`. Removes the manual bot-bounce step from the runbook. |
| ⚙️ Shipped (server-side; awaiting Spotify Developer App seed) | **Spotify URLs + URL-resolved playlists (v4.2)** | See §9. lavasrc plugin loaded on Lavalink, GSM secret container in place, bot iterates playlists with a 100-track cap and renders a summary embed. Spotify URLs work the moment the user seeds the `spotify-client-credentials` secret per `docs/bootstrap.md`. |
| 📋 Backlog | **Valheim mod support** | BEPINEX or ValheimPlus loader; mod files via GCS bucket; bot command to apply pending mods at next restart. Design open. |
| 📋 Backlog | **Stop+reboot persistence validation** | Stress-test that the data disk survives stop/start cycles correctly across all the things that can boot the VM. |
| 📋 Backlog | **Load testing** | What's the e2-standard-2 ceiling? Does world building under multi-player load degrade? Ticket: pick a stress profile, run for 30 min, decide if we bump to e2-standard-4. |
| 🔮 Future | **Per-user saved playlists** | Would require persistent state (Firestore or GCS object). Bigger architectural commitment than URL-resolved playlists; punt until URL-resolved playlists ship and we measure how often users want this. |
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
| 2026-04-29 | **Lavalink jar under systemd, NOT Docker** | The official Docker images had broken bundled JDKs (random ClassFormatErrors). Direct jar + `openjdk-17-jre-headless` is more reliable and removes a Docker dep we don't otherwise need on that VM. |
| 2026-04-29 | **JVM `-Djava.net.preferIPv4Stack=true`** | GCE silently drops IPv6 egress for our project; without this flag the JVM hangs on TLS handshakes to YouTube/Discord. |
| 2026-04-29 | **Lavalink 4.2.2 specifically (DAVE-aware) + Wavelink 3.5+ (sends channelId + DAVE)** | Discord rolled out E2EE/DAVE during v4.0 dev. Older Lavalink versions throw close code 4017; older Wavelink versions don't send `channelId` so Lavalink rejects voice payloads with 400. Specific pin combo is the only thing that worked. |
| 2026-04-29 | **Idle watcher iterates targets in one Cloud Function, not per-target functions** | One scheduler, one zip, one IAM surface. Adding a target is a 3-line change in `main.py`. Cost is identical (under free tier either way). |
| 2026-04-29 | **Idle watcher state: one bucket, multiple objects (`state-<target>.json`), not multiple buckets** | Cheaper, simpler IAM (one `objectUser` binding), trivial to enumerate. |
| 2026-04-29 | **Promote Spotify URL/playlist support to next priority** | Friend group asked. URL resolution is a much smaller commitment than per-user libraries (no persistence) and fits the existing services/music boundary. |
| 2026-04-30 | **Hard 100-track cap on playlist enqueue, surface truncation** | Friend-group playlists are 10-30; 100 is comfortably above that and prevents a 5000-track YouTube auto-mix from ever filling the queue. Cap applies to YouTube + Spotify + albums uniformly. |
| 2026-04-30 | **Single GSM secret with JSON for Spotify credentials, not two split secrets** | One IAM binding, atomic rotation (no client_id/secret pairing race), mirrors `discord-bot-secrets` shape. |
| 2026-04-30 | **lavasrc Spotify source disabled by default; gated on `LAVASRC_SPOTIFY_ENABLED` from fetch-secrets** | Lavalink VM should boot fine even when the Spotify secret has no versions yet (fresh apply, user hasn't registered Dev App). YouTube/HTTP queries keep working; Spotify URLs error cleanly. Removes a "first apply bricks the bot" footgun. |
| 2026-04-30 | **Single `play()` returning `PlayResult` discriminated union, not separate `play_track`/`play_playlist`** | Cog branches on `playlist_title is not None`; one function, one shape, easier cog tests, easier to evolve when we add e.g. live-stream URL handling. |
| 2026-04-30 | **Fail-clean (don't fall back to YouTube search) when a Spotify URL fails to resolve** | Falling back would silently play a wrong track. Better to surface the failure so the user can fix the URL or seed credentials. |
| 2026-05-02 | **Status daemon switched from periodic re-tail to follow-stream** | The `--tail 500` model lost track of player_count after ~15-30 min of quiet play (the most-recent "now N player(s)" line scrolled off). Watcher then false-stopped a live session. New design opens one long-lived `docker compose logs --follow`, ingests line-by-line, and persists state across reconnects. Side benefit: ~10× lower CPU than re-spawning the subprocess every 30s. |
| 2026-05-02 | **`empty_checks_to_stop` bumped 2 → 4** | Defense-in-depth against future regressions in any probe. With 30-min cron, idle window is 90-120 min. The daemon bug is fixed but the buffer is cheap insurance. Applied uniformly to Valheim and Lavalink targets; if Lavalink's window ever needs to be tighter, split into per-target variables. |
| 2026-05-02 | **Lavalink probe URL: `/v4/players` → `/v4/stats`** | The original endpoint requires a sessionId (`/v4/sessions/{id}/players`), and the watcher has no session of its own — every tick 404'd silently. The watcher correctly treats 404 as 'unknown' so the bug surfaced as 'lavalink VM never auto-stops' rather than a stop-storm. `/v4/stats.playingPlayers` is the canonical session-less aggregate. |
| 2026-05-02 | **Crossplay disabled (`CROSSPLAY=false`)** | Players reported intermittent ~20s lag spikes mid-session. Container logs traced to PlayFab relay reconnects (`code 4098: invalid handle` + ResetParty/JoinParty cycle, with the relay edge at `*.cloudapp.azure.com` in Microsoft's Azure North-Central-US). Direct Steam P2P removes the middlebox. Trade-off: no Xbox/Game Pass crossplay; friend group is Steam-only so trade-off is free. World saves unaffected — `.db`/`.fwl` format is identical between modes. Players now connect via Valheim → Join Game → Join IP → `<public_ip>:2456`. |
| 2026-05-03 | **Status daemon switched from log-scraping to Steam A2S query** | Players reported being booted mid-session by the watcher even after the 2026-05-02 follow-stream rewrite. Daemon journal showed `docker compose logs --follow` exiting with code 0 at random — both during the boot race and during normal runtime — losing player events in the 5-second reconnect gaps. Root cause is structural: docker's `--follow` semantics aren't actually a guaranteed continuous stream. The new daemon queries the game itself via Steam's A2S_INFO protocol (UDP localhost:2457). This is the canonical Server Browser query that the dedicated server itself answers; it bypasses log parsing entirely. We previously avoided A2S because crossplay/PlayFab made it unreliable, but with crossplay off (2026-05-02 decision above) the protocol responds correctly — verified against the live server. Implementation is stdlib `socket` + byte-slicing (~30 lines for the protocol; avoids the Debian-12 PEP-668 pip-install plumbing the python-a2s lib would have required). HTTP `/status.json` schema unchanged, so the bot's `services/server_query.py` and the watcher's `_probe_valheim` need zero changes. |
| 2026-05-03 | **Idle watcher PAUSED via Cloud Scheduler `paused=true`** | After three rounds of incremental fixes (truncation → follow-stream → A2S) the user reported the watcher still misbehaving and requested a hard off-switch. The `paused` flag on `google_cloud_scheduler_job` is a clean kill: function and IAM stay deployed, only the cron stops firing. Plumbed through as `idle_watcher_paused` env-level variable + `paused` module variable so the toggle lives in `terraform.tfvars` (one-line flip, version-controlled, reversible). While paused, both on-demand VMs (Valheim, Lavalink) stay up until manually stopped. Cost trade-off: at e2-standard-2 ($0.07/hr) + e2-small ($0.014/hr), an unmonitored idle pair runs ~$2/day. Acceptable until we either trust the A2S daemon enough to re-enable, or pivot to a different idle-detection strategy. **Re-enabling is `idle_watcher_paused = false` in `terraform.tfvars` + `terraform apply`.** |
| 2026-05-03 | **Server-side BepInEx + Jotunn shipped; PlanBuild server-side runs in degraded state ("Option A")** | Goal: enable PlanBuild blueprints for the friend group. Mod is primarily client-side (each player installs locally for the planning hammer / blueprint rune); server-side install would have unlocked the blueprint marketplace. We pinned PlanBuild 0.18.4 + Jotunn 2.28.0 + HookGenPatcher 0.0.4 (matches the Thunderstore manifest for MathiasDecrock/PlanBuild, which is the same project as the sirskunkalot/PlanBuild GitHub repo). Discovered lloesche's `merge_mod` does an atomic `mv bepinex.tmp bepinex` rename that orphans any bind mount on `bepinex/BepInEx/patchers/`, defeating every approach to inject a patcher into the active dir. HookGenPatcher therefore doesn't load on the server. Jotunn loads cleanly (registers PlanBuild's 2 custom RPCs), PlanBuild's `Awake()` throws `TypeLoadException`, marketplace functionality unavailable. Workarounds (custom Dockerfile, two-boot dance) cost 30+ min implementation each and add complexity to every cold start. **Decision: ship as-is.** Friend group installs PlanBuild client-side via Thunderstore Mod Manager / r2modman; basic blueprint features work; marketplace deferred. If we ever want it, the dep is already downloaded — only the loading mechanism needs to change. |
| 2026-05-03 | **Disable Valheim auto-update via `UPDATE_CRON=""`** | The lloesche image's daily `0 5 * * *` Valheim update can break BepInEx mods (Valheim assembly version changes invalidate generated hooks; PlanBuild needs ~1-3 days for a fixed release after each Valheim patch). With mods installed, "boot-clean" beats "always-latest." Updates are now manual: SSH in, re-enable cron temporarily OR `steamcmd app_update 896660 validate` after confirming PlanBuild has shipped a fix. |
| 2026-05-03 | **Allow metals through portals via `SERVER_ARGS="-modifier portals casual"`** | Friend-group quality-of-life. Removes the original-game restriction that copper/tin/iron/silver/black-metal can't transport through portals. Exposed via lloesche's `SERVER_ARGS` env passthrough, which the image appends to the Valheim binary's command line. Verified in the running process's `argv`. Other modifiers can be space-separated; reverting is `SERVER_ARGS=""`. |
| 2026-05-03 | **All `[Server Settings] Allow X = false` flipped to `true` in PlanBuild config** | Investigating "terrain tools don't work" friend feedback led to discovering the server-side config keys we'd missed (PRD-correction: I'd previously claimed they didn't exist). PlanBuild's `marcopogo.PlanBuild.cfg` has 5 `Allow X` flags in `[Server Settings]`; 3 default to `false` (direct-build, terrain-tools, server-side blueprints). Friend-group server is private + trusted, so flip all to `true`. Live-edited the .cfg on the persistent disk (instant fix once PlanBuild reads the value, latest at next container restart) and added an idempotent `sed` patch to `install-mods.sh` so fresh VMs get the same settings (with a one-bounce caveat documented in runbook §6.6 since PlanBuild creates the .cfg on its FIRST plugin load, not on lloesche's bootstrap). |
| 2026-05-04 | **Idle watcher RE-ENABLED (`idle_watcher_paused = false`)** | A2S daemon verified accurate across yesterday's testing: live container bounces, multi-player connection scenarios, post-mod-install boots — daemon's `player_count` always matched a raw A2S probe. Original failure modes are structurally gone (log-tail truncation, follow-stream fragility, Steam-only regex mismatch — A2S asks the game itself, no log parsing). Manual scheduler fire after un-pause: both targets reported truthful decisions (`[valheim] empty 1/4`, `[lavalink] empty 1/4` — both VMs were RUNNING but actually idle). The lavalink path also works correctly for the first time today (was 404'ing on the wrong endpoint before yesterday's fix). Watcher kept at `empty_checks_to_stop=4` + 30-min cron (90-120 min idle window); plenty of margin for a single false-empty to be benign. PlanBuild `Awake` failure (Option A) doesn't affect the daemon — A2S is independent of any mod state. |
| 2026-05-07 | **Lavalink connect timeout: 30s → 90s** | Pinpointed when investigating "music bot not starting" report: every `/music play` failed with `Lavalink node ... did not connect within 30.0s`. Sequence: watcher stopped Lavalink (expected, idle 90min) → user `/music play` → bot called `instances.start` + replied "wait ~90s" → user retried within ~30s → bot saw VM RUNNING + tried `Pool.connect` → Wavelink WS handshake hit 30s timeout because Lavalink JVM was still loading (BepInExPack + lavasrc pulls from Thunderstore on fresh boot). Bumped to 90s which matches the cog's user-facing "wait ~90s" message. Half-broken Lavalink still fails fast via `/v4/info` health check. |
| 2026-05-08 | **Strict `/livez` liveness probe + Cloud Run kill-and-replace** | The 2026-05-08 incident: bot's Discord gateway WS silently degraded mid-day. `bot.is_ready()` (sticky once True) and `bot.latency` (caches the last heartbeat ack) both kept reporting "fine" for ~5 hours while the bot was actually unable to receive interactions. Cloud Run's default TCP probe was satisfied that uvicorn was listening, so the wedged container ran forever. Each retry on the gateway side burned Discord IDENTIFY budget; eventually we hit a 429 storm during a hotfix bounce. Fix: new `/livez` endpoint (in `bot/src/http.py`) returns 503 when bot.ws is closed OR last gateway event >90s old. Cloud Run `liveness_probe` hits `/livez` every 60s; 5 consecutive 503s (~5 min) kills the container and `min-instances=1` brings up a fresh replacement. `/health` stays as a soft info endpoint (always 200, debuggable via curl). 5-min grace is intentional: long enough to ride out a Cloudflare blip without restart-flapping (which would replay the IDENTIFY-rate-limit storm), short enough that a real wedge gets fixed without operator intervention. Decision-logic is a pure function over a `_LivenessSnapshot` dataclass; 10 unit tests cover every failure branch. |
| 2026-05-09 | **`/livez` freshness signal: `on_socket_event_type` → `KeepAliveHandler._last_recv`** | Regression I shipped yesterday: the freshness signal used a custom `on_socket_event_type` listener which only fires for DISPATCH ops, NOT heartbeats. Quiet guild stretches (no message/presence activity) made the timestamp go stale within 90s even though the WS was fine. After 5 consecutive 503s Cloud Run kill-looped the bot every ~5 min during low-traffic periods. Fix: read discord.py's KeepAliveHandler `_last_recv` directly — that attribute is bumped via `tick()` on every received WS message of any kind (heartbeat ACKs, DISPATCH, reconnect signals). Discord sends heartbeats every ~41s so `_last_recv` stays fresh on a healthy connection regardless of guild activity. Hotfix while rolling out: temporarily point probe at `/health` (always 200) to break the kill loop; restore to `/livez` once the new code revision is verified. |
| 2026-05-09 | **youtube-plugin: 1.13.5 → 1.18.1** | User reported songs cutting at 12-13s. youtube-plugin 1.13.5 had broken on certain YouTube response shapes. Bumped to 1.18.1 in `server/lavalink/application.yml`; Lavalink VM picks up new plugin on next cold-start (downloaded from Maven repo at JVM boot). |
| 2026-05-10 | **`_drop_stale_node` uses Wavelink's `close(eject=True)` (was no-op pop on a copy)** | Bug present since the 2026-05-04 stale-session detection landed: `wavelink.Pool.nodes` is a `classproperty` that returns `cls.__nodes.copy()` — a throwaway dict. Our `Pool.nodes.pop(_NODE_IDENTIFIER, None)` was popping from the copy, NEVER affecting the real `_Pool__nodes` dict. So old identifiers stayed registered indefinitely; subsequent `Pool.connect(new_node)` either silently rejected or got into a confused state where the WS handshake never completed. User-visible: every Lavalink VM-cycle required a manual Cloud Run bounce to clear in-memory state. Confirmed via `wavelink.Pool.nodes is wavelink.Pool._Pool__nodes` returning False at runtime. Fix: `await node.close(eject=True)` (the `eject` param, added in Wavelink 3.2.1, makes `close()` itself remove from the real `_Pool__nodes`). Defensive fallback also touches `_Pool__nodes` via name-mangling if `close()` raises pre-eject — private API, accept the version-coupling risk to avoid stuck Pool state. |
| 2026-05-12 | **Bot + Lavalink folded onto a single always-on `e2-small` (gcp-bot-vm), Cloud Run scaled to 0** | Cost analysis (recalibrated from the wrongly-low $23-36/mo guess to actual $72-77/mo): Cloud Run `min=1` for the bot ran ~$13/mo, Lavalink standalone e2-small idle ran ~$35/mo, and there was no UX win from running Lavalink on-demand — every first `/music play` of a session paid 60-90s cold-start. Co-tenancy onto one e2-small saves ~$35/mo AND eliminates the cold-start: bot talks to Lavalink at `localhost:2333` (no firewall hop, JVM is always warm). Trade-offs: (a) single VM is now a SPOF for both features, accepted given hobby scale and the existing systemd watchdog; (b) bot deploy mechanism shifts from Cloud Build → Cloud Run revision to manual `ssh; git pull; poetry install; systemctl restart bot` — fine for friend-group cadence (deploys every few days at most). Cutover was parallel-new: TF created the new VM with bot+Lavalink already running, Cloud Run was scaled to `min=0` to release the Discord gateway WS, the new VM took over instantly (single-IDENTIFY window, no double-session race). Idle watcher's Lavalink target was dropped via `count=` conditional in the same apply. Cloud Run service is kept at `min=0, max=1` as a rollback option (one-line flip back to `min=1`) for ~1 week; gcp-lavalink-vm module is kept for the same reason. After the soak: destroy both. Several incidental fixes shipped with the migration: Python pin relaxed from `^3.12` → `^3.11` for Debian 12 compatibility; google-auth pinned `>=2.46` via post-`poetry install` pip upgrade in the startup-script (2.45.0 had an `_prepare_request_for_mds` regression that crashed `SecretManagerServiceClient()` on GCE); bot.env render switched from `file()` → `templatefile()` so `${var}` placeholders actually substitute (initial cut crashed in Pydantic parsing `${valheim_status_http_port}` as an int); systemd watchdog (`bot-watchdog.timer` curling `/livez` every 60s; 5 consecutive 503s → `systemctl restart bot`) replicates Cloud Run's `liveness_probe` kill-and-replace mechanic. |
| 2026-05-13 | **Voice-gateway recovery: event-driven + heartbeat (`should_recover` pure function); 1 retry per track, then skip** | First post-cutover incident: after ~20min of music, Discord's voice server reset the UDP path (Lavalink/Koe logged `recvAddress(..) Connection reset by peer`). Lavalink kept advancing player position against its own clock so the bot had no signal — users heard silence for ~3min before manually running `/music stop`. Two failure shapes to cover: (A) voice gateway WS closes cleanly with a recoverable code (4006/4014/4015) — surfaces as `on_wavelink_websocket_closed`; (B) transport reset where position keeps advancing — surfaces only via Lavalink stats. Shipped both layers in one PR: (1) event handler for `on_wavelink_websocket_closed` on recoverable close codes; (2) 2-second heartbeat task that polls `/v4/sessions/{sid}/players/{gid}` for `state.connected` AND `/v4/stats` for `frameStats.deficit` growth ≥25 frames/sample, feeds successive `VoiceHealthSnapshot`s into pure `should_recover()`. Decision function returns `NONE`/`RECOVER`/`GIVE_UP_AND_SKIP`; mirrors `_LivenessSnapshot` from the 2026-05-08 work — 17 unit tests cover every branch. Recovery routine: `player.disconnect()` → `voice_channel.connect(cls=Player)` → `player.play(saved_track, start=saved_position)` so the user picks up where audio dropped. **Per-track budget = 1 retry, then skip** (decision was: don't reset on healthy-audio gaps within the same track — a track that wedges twice is structurally bad, accept the loss and move on rather than spinning recovery). All recovery actions post visible messages to the music channel ("🔁 Audio dropped on **<track>**, reconnecting at <m:ss>…" and "⏭️ Couldn't keep **<track>** playing, skipping."). Detection window: ~4s of wedge before recovery fires, ~7s total to playing-again. Bumped Lavalink's `playerUpdateInterval` from 5s to 1s in `application.yml` to feed the heartbeat fresh data; incidentally unblocks a future "now-playing widget with progress bar + control buttons" PR. The 60s recovery-throttle prevents the heartbeat from re-firing on a still-handshaking voice link. Frame-deficit signal is `/v4/stats` aggregate (per-node) — a known limitation for the rare multi-guild-concurrent case; documented and acceptable for hobby scale. |

---

## 9. Spotify URLs + URL-resolved playlists (v4.2 — shipped pending credential seed)

Status: bot, infra, and Lavalink server-side all shipped. Spotify URLs
will start working the moment a Spotify Developer App is registered
and the `spotify-client-credentials` GSM secret is seeded — see
[`docs/bootstrap.md`](bootstrap.md#spotify-developer-app-credentials-optional).

### What it covers (and what it doesn't)

In scope:
- `/music play <spotify-track-url>` — resolves the track via Spotify's API, plays the corresponding audio (which Lavalink finds on YouTube under the hood — Spotify itself doesn't expose stream-able audio).
- `/music play <spotify-playlist-url>` — resolves the playlist, enqueues each track. Same goes for Spotify album URLs.
- `/music play <youtube-playlist-url>` — already partially works via Lavalink's youtube-source plugin; verify and document.
- A "+N tracks queued" embed when a playlist resolves to many tracks (avoid spamming the channel with N "Now playing" messages).

Out of scope (still):
- Per-user saved playlists / a library — needs persistent state, separate ticket.
- Spotify *playback* (the app/device flow) — we're not a Spotify Connect target. We resolve metadata and play YouTube audio.
- Apple Music, Deezer, etc. — lavasrc supports them, but we don't have demand. Add when asked.

### Architecture sketch

```
                 /music play <spotify-url>
                       │
                       ▼
              ┌────────────────────┐
              │ Bot music cog      │
              │ (no change to API) │
              └────────┬───────────┘
                       │ services.music.play(query)
                       ▼
              ┌────────────────────┐
              │ services/music.py  │
              │ Wavelink.search()  │
              └────────┬───────────┘
                       │ Lavalink REST /loadtracks
                       ▼
              ┌────────────────────┐  Spotify Web API   ┌──────────────┐
              │ Lavalink           │───────────────────►│  Spotify     │
              │  + lavasrc plugin  │  (client_credentials)│ /tracks etc.│
              └────────┬───────────┘                    └──────────────┘
                       │ For each resolved track:
                       │   YouTube fallback search
                       ▼
              ┌────────────────────┐
              │ youtube-source     │
              │  plugin (existing) │
              └────────────────────┘
```

The bot's public surface barely moves. `/music play` already accepts arbitrary strings; lavasrc routes Spotify URLs to its resolver, falls back to YouTube for the actual audio. Most of the work is on the Lavalink side + secret seeding.

### What changes

| Layer | Change |
|---|---|
| Lavalink VM | Add `lavasrc` plugin to `application.yml` plugins list. Configure with `spotify.clientId` / `spotify.clientSecret` (env-substituted by Spring). |
| Lavalink VM | `fetch-secrets.sh` learns to fetch a new GSM secret (`spotify-client-credentials`) and write it to `/etc/lavalink/secret.env` alongside the existing password. |
| Terraform — `gcp-lavalink-vm` | New `google_secret_manager_secret` for `spotify-client-credentials`. Lavalink VM SA gets `secretmanager.secretAccessor` on it (secret-scoped). Optional input variable: `spotify_credentials_secret_id` (defaulted, configurable). |
| Bot — `services/music.py` | Probably zero changes — Wavelink already returns `Playlist` objects when the search resolves to one. The cog needs to handle the multi-track-enqueue path. |
| Bot — `cogs/music.py` | New embed for "Added N tracks from <playlist title>". Loop and append to `wavelink.Queue` for non-trivial playlist sizes. |
| Bot — settings | No new env vars on the bot side (Lavalink owns the Spotify credentials, not the bot). |
| Idle watcher | No change. The `/v4/players` probe is source-agnostic. |
| Tests | `cogs/music.py` test for the playlist-enqueue branch. Lavasrc itself stays unmocked at the lavalink boundary; the cog just sees Wavelink's result objects. |

### Cost / operational shape

- **No new always-on resources.** Lavalink already runs on-demand; lavasrc is a plugin, not a separate process.
- **Spotify Developer app is free** at the rate limits we'll hit (per-track metadata calls a few times per day).
- **Idle-watcher behavior is unchanged** — same e2-small VM, same auto-stop window.

### Open questions to resolve before coding

1. Should `/music play <playlist-url>` enqueue all tracks immediately, or paginate (e.g. first 50, "load more" button)? Bias toward "all immediately" — playlists in our friend group are 10-30 tracks, not 1000.
2. Should we cap the playlist size to prevent a runaway 5000-track YouTube playlist from being enqueued? Probably yes, with a friendly error at e.g. 200 tracks.
3. Volume/loop/shuffle behavior across playlist enqueue — no new design; existing semantics carry over.
4. Spotify credential rotation cadence — we don't rotate the bot token or the Lavalink password actively; treat the Spotify credentials the same.
5. Failure isolation — if lavasrc fails to resolve a Spotify URL, does the cog fall back to a string search, or fail clean? Lean fail-clean for clarity.

These get nailed down in the v4.2 grilling round.

---

## 10. Reading list — when joining this codebase

1. This PRD (you are here)
2. [`docs/architecture.md`](architecture.md) — diagrams + interface boundaries
3. [`docs/runbook.md`](runbook.md) — what to do when something is wedged
4. [`docs/bootstrap.md`](bootstrap.md) — one-time GCP setup
5. [`bot/README.md`](../bot/README.md) — bot-only quickstart
6. [`server/README.md`](../server/README.md) — what runs inside the Valheim VM
7. [`infra/README.md`](../infra/README.md) — TF layout
8. [`CHANGELOG.md`](../CHANGELOG.md) — what changed when, and why

When making a non-trivial change, update this PRD first. Code follows.
