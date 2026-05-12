# Architecture

## Components

- **Bot + Lavalink VM (`bot/` + `server/bot-vm/` + `infra/modules/gcp-bot-vm`)** — single always-on GCE `e2-small` in `us-central1-a` co-tenanting two systemd services: `bot.service` (Python + discord.py[voice], Discord Gateway) and `lavalink.service` (Lavalink jar). Slash-only. Handles `/ping`, `/info`, the `/valheim status|start|stop` group, and the `/music *` group. Bot connects to Lavalink at `localhost:2333`; port 2333 is **not** exposed externally. Deploy is manual: `ssh; cd /opt/mr-swede; git pull; poetry install; sudo systemctl restart bot`. Cog architecture means the next feature is one new file in `bot/src/cogs/` plus (optionally) one new Terraform module.
- **Valheim VM (`server/` + `infra/modules/gcp-valheim-vm`)** — single GCE `e2-standard-2` in `us-central1-a` running Docker Compose + `lloesche/valheim-server`, world data on a separately-attached `pd-balanced` persistent disk. Crossplay OFF (Steam-only); players join via direct IP `<public_ip>:2456`.
- **Bot runtime — legacy (`infra/modules/gcp-bot-runtime`)** — Cloud Run service `mr-swede` (Artifact Registry + Cloud Build trigger + IAM). **Scaled to `min=0, max=1`** as of 2026-05-12: kept as a one-flip rollback option (re-set `min=1`) but does not serve traffic. Will be retired once the bot-vm has soaked for ~1 week.
- **Lavalink VM — retired (`server/lavalink/` + `infra/modules/gcp-lavalink-vm`)** — was a single GCE `e2-small` running Lavalink standalone with public port 2333. Folded into the bot VM (above) on 2026-05-12 to eliminate cold-start UX cost and ~$5/mo idle-watcher overhead. Module + `server/lavalink/` artifacts are kept as the source of Lavalink config (consumed by `gcp-bot-vm`); the standalone VM resource will be destroyed after the bot-vm soak.
- **Backups** — daily GCE disk snapshots + `gsutil rsync` of Valheim world files to a GCS bucket. Bot+Lavalink VM is stateless (config in git, secrets in GSM), no backups. *(Not yet built; will land as `infra/modules/gcp-backups`.)*
- **Idle watcher (`infra/modules/gcp-idle-watcher`)** — Cloud Scheduler → Cloud Function. Now single-target: polls Valheim's status HTTP endpoint (`/status.json`) and stops the Valheim VM after N consecutive empty checks. The Lavalink target was dropped on 2026-05-12 (Lavalink is now co-tenanted on the always-on bot VM and never idle-stops). The multi-target shape is retained (`count`-guarded resources) so a second target can be re-added with a one-line variable flip. State is keyed per target (`state-valheim.json`) in a single GCS bucket.

## Valheim VM topology

```
                 ┌────────────────── Internet ──────────────────┐
                 │                                                │
                 │   UDP 2456-2458 (Valheim, public)              │
                 │   TCP 22  (only via IAP tunnel, 35.235.240.0/20)
                 │                                                │
       ┌─────────▼────────────────────────────────────────────────▼─────┐
       │                  valheim-vpc / valheim-subnet                  │
       │                                                                │
       │   ┌──────────────────────────────────────────────────────┐    │
       │   │  google_compute_instance.valheim                      │    │
       │   │   • e2-standard-2  • Debian 12 boot disk (10GB)       │    │
       │   │   • shielded VM    • ephemeral public IP              │    │
       │   │   • tag: valheim-server                               │    │
       │   │   • SA: valheim-vm-sa  (logging.logWriter +           │    │
       │   │     monitoring.metricWriter, secret-scoped reader)    │    │
       │   │                                                       │    │
       │   │   /opt/valheim/data ──► attached pd-balanced disk     │    │
       │   │     (20GB, prevent_destroy = true)                    │    │
       │   │                                                       │    │
       │   │   docker compose up:                                  │    │
       │   │     lloesche/valheim-server                           │    │
       │   │     env_file: world.env + secret.env                  │    │
       │   └──────────────────────────────────────────────────────┘    │
       │                            ▲                                   │
       │                            │  metadata server                  │
       │                            │  + SA token                       │
       │                            │                                   │
       └────────────────────────────┼───────────────────────────────────┘
                                    │
                       ┌────────────▼────────────────┐
                       │ Secret Manager              │
                       │   valheim-server-password   │
                       │     (regional replica:      │
                       │      us-central1)           │
                       └─────────────────────────────┘
```

### Cloud-init bootstrap order

1. apt: install Docker CE + compose plugin
2. format (first boot only) + mount the data disk at `/opt/valheim/data`
3. drop `docker-compose.yml`, `fetch-secrets.sh`, and the two systemd units in place
4. `systemctl enable --now valheim.service`
   → `Requires=valheim-fetch-secrets.service` runs first
   → fetches `SERVER_PASS` from Secret Manager, writes `/etc/valheim/secret.env`
   → `docker compose up` reads `world.env` + `secret.env`

The startup-script is rendered by `templatefile()` with the four runtime artifacts (`server/docker-compose.yml`, `server/scripts/*`) inlined as base64. The metadata key is `startup-script` (not `user-data`/cloud-init) because GCP's standard Debian image doesn't include cloud-init -- google-guest-agent runs the startup-script on every boot. The script is idempotent; `terraform apply` pushes new template content in-place, and the next reboot picks it up. No VM replacement needed.

## Bot + Lavalink VM topology (current; v4.3 — 2026-05-12)

```
                 ┌────────────────── Internet ──────────────────┐
                 │                                                │
                 │   Discord Gateway WS (outbound from bot)       │
                 │   GCP APIs (Compute, GSM, Logging — outbound)  │
                 │   TCP 22  (only via IAP tunnel, 35.235.240.0/20)
                 │                                                │
       ┌─────────▼────────────────────────────────────────────────▼─────┐
       │                  valheim-vpc / valheim-subnet                  │
       │                  (shared with the Valheim VM)                  │
       │                                                                │
       │   ┌──────────────────────────────────────────────────────┐    │
       │   │  google_compute_instance.bot_vm                       │    │
       │   │   • e2-small  • Debian 12 boot disk (15GB)            │    │
       │   │   • shielded VM    • ephemeral public IP              │    │
       │   │   • tag: bot-vm    • always-on (no idle-watcher)      │    │
       │   │   • SA: mr-swede-sa  (reused from gcp-bot-runtime;    │    │
       │   │     instance-scoped compute.instanceAdmin on the      │    │
       │   │     Valheim VM, GSM accessor on 3 secrets)            │    │
       │   │                                                       │    │
       │   │   /opt/lavalink (user: lavalink)                      │    │
       │   │     • Lavalink.jar 4.2.2  • application.yml           │    │
       │   │                                                       │    │
       │   │   /opt/mr-swede (user: bot — git clone of repo)       │    │
       │   │     • bot/.venv/  (poetry in-project venv)            │    │
       │   │     • git pull + systemctl restart bot = deploy       │    │
       │   │                                                       │    │
       │   │   systemd:                                            │    │
       │   │     lavalink-fetch-secrets ─► lavalink.service        │    │
       │   │       java -Xmx512m -Djava.net.preferIPv4Stack=true   │    │
       │   │            -jar /opt/lavalink/Lavalink.jar            │    │
       │   │       (binds 0.0.0.0:2333 but firewall denies inbound)│    │
       │   │     bot-fetch-secrets ─► bot.service                  │    │
       │   │       python -m src.main  (Discord gateway WS)        │    │
       │   │       env: LAVALINK_HOST=localhost LAVALINK_PORT=2333 │    │
       │   │     bot-watchdog.timer  (every 60s)                   │    │
       │   │       curl localhost:8080/livez; 5x fail → restart    │    │
       │   │       (replaces Cloud Run's kill-and-replace probe)   │    │
       │   └──────────────────────────────────────────────────────┘    │
       │                            ▲                                   │
       │                            │  metadata server                  │
       │                            │  + SA token                       │
       │                            │                                   │
       └────────────────────────────┼───────────────────────────────────┘
                                    │
                       ┌────────────▼────────────────┐
                       │ Secret Manager              │
                       │   discord-bot-secrets       │
                       │   lavalink-server-password  │
                       │   valheim-server-password   │
                       │   spotify-client-credentials│
                       └─────────────────────────────┘
```

The bot VM has no persistent disk: bot state lives in Discord and Lavalink jar caches; both are disposable. Deploy is manual (intentional — friend-group cadence): SSH in, `git pull`, `poetry install`, `sudo systemctl restart bot`. `bot-watchdog.timer` curls `/livez` every 60s and restarts the bot on 5 consecutive failures — the systemd equivalent of Cloud Run's `liveness_probe` kill-and-replace. `-Djava.net.preferIPv4Stack=true` avoids a JVM hang on GCP egress where IPv6 is silently dropped. The youtube-source plugin is bundled in via `application.yml` plugin coordinates so we're not locked into Lavalink's stale built-in YouTube source.

**Why co-tenant.** Lavalink standalone cost ~$35/mo idle (e2-small) and added 60-90s cold-start UX latency to `/music play` (idle-watcher had stopped the VM). The bot's Cloud Run service was ~$13/mo at `min=1` (it has to be — Discord drops idle gateway sessions). Folding both onto a single always-on e2-small saved ~$35/mo and made `/music play` instant (localhost connection, JVM already warm). The Cloud Run service is kept around at `min=0` as a rollback option for ~1 week; will be retired after the soak.

## Bot ↔ Lavalink data flow (post-2026-05-12 co-tenant)

```
   /music play <q>     ┌─────────────┐
   ───────────────────►│ Discord     │
                        │ Gateway     │
                        └──────┬──────┘
                               │ INTERACTION_CREATE
                               ▼
                        ┌─────────────┐
                        │ bot         │  (LAVALINK_HOST=localhost
                        │ (music cog) │   short-circuits "ensure VM RUNNING")
                        └──────┬──────┘
                               │ Wavelink (v4 WS+REST + voice forwarding)
                               │ over loopback — no firewall hop
                               ▼
                        ┌─────────────┐  YouTube/SC HTTPS    ┌──────────────┐
                        │ Lavalink    │─────────────────────►│ Audio source │
                        │ (same VM,   │                      └──────────────┘
                        │  localhost) │
                        └──────┬──────┘
                               │ Opus over Discord voice gateway
                               │ (DAVE/E2EE since Discord 2025 rollout)
                               ▼
                        ┌─────────────┐
                        │ Voice       │
                        │ channel     │
                        └─────────────┘
```

The bot's role is a thin orchestrator: handle the slash command, hand the URL/query to Lavalink over loopback. Audio bytes never traverse the bot — they flow Lavalink → Discord directly. That's the whole reason Lavalink exists.

The `/valheim *` group still talks to GCP Compute API to start/stop the Valheim VM (that VM remains on-demand). What changed for `/music *` is that the cog used to do the same dance for the Lavalink VM (instances.start → poll until RUNNING → wait for JVM to warm → connect); with Lavalink co-tenanted, the cog now sees `LAVALINK_HOST=localhost` and skips the entire compute-API path. Music feels instant for the first play of a session instead of taking 60-90s.

## Key interface boundaries

The bot exposes service modules with intentionally narrow public surfaces:

- [`bot/src/services/compute.py`](../bot/src/services/compute.py) — three free functions (`describe_instance`, `start_instance`, `stop_instance`) returning a frozen `InstanceState` dataclass. GCE-specific via `google-cloud-compute`. Used by both `/valheim *` and `/music *`.
- [`bot/src/services/server_query.py`](../bot/src/services/server_query.py) — one function `fetch_status(host, port)` returning a frozen `LiveStatus` dataclass. HTTP fetch from the Valheim VM's log-scraping daemon (`server/scripts/status-server.py`). Replaced an earlier Steam-A2S implementation that broke when Valheim's crossplay/PlayFab transport rolled out.
- [`bot/src/services/music.py`](../bot/src/services/music.py) — a thin Wavelink wrapper: `connect_node(client, host, port, password)` (idempotent, polls until CONNECTED), `play(voice_channel, query, requester_id=None)`, plus a few queue helpers. The cog calls these and adds no Discord-specific glue beyond what Wavelink already provides.
- [`bot/src/utils/checks.py`](../bot/src/utils/checks.py) — `requires_channel(setting_attr)` decorator factory. Pure-logic predicate so cogs can declaratively gate to a configured channel without bespoke per-command checks.

**Swapping clouds.** We deliberately picked a single concrete impl over an abstract `Protocol`/ABC up front. To swap to AWS/Hetzner/etc. later: rename `compute.py` → `gcp_compute.py`, lift the function signatures into a `Protocol` in a new `compute.py`, add an `aws_ec2.py` parallel impl, and update one import line per call site (currently `bot/src/cogs/valheim.py` and `bot/src/cogs/music.py`). The `InstanceState`/`GameState` dataclasses stay unchanged — providers map their native types into them.

The VM's runtime artifacts in `server/` are vendor-neutral on their own (only `fetch-secrets.sh` is GCP-specific, and there's one per VM type — both single files).

World-management surface (rewrite `world.env`, restart the service over SSH) is intentionally *not* in scope yet — it'll appear if/when we add `/valheim worlds switch <name>`.
