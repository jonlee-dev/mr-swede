# Architecture

## Components

- **Bot (`bot/` + `infra/modules/gcp-bot-runtime`)** — Python + discord.py[voice] (Gateway), deployed to Cloud Run with `min-instances=1`. Slash-only. Handles `/ping`, `/info`, the `/valheim status|start|stop` group, and the `/music *` group. Runtime infra (Cloud Run service, Artifact Registry, Cloud Build trigger, IAM) is fully Terraform-managed; image lifecycle is owned by Cloud Build (TF `ignore_changes` on the image field). Cog architecture means the next feature is one new file in `bot/src/cogs/` plus (optionally) one new Terraform module.
- **Valheim VM (`server/` + `infra/modules/gcp-valheim-vm`)** — single GCE `e2-standard-2` in `us-central1-a` running Docker Compose + `lloesche/valheim-server`, world data on a separately-attached `pd-balanced` persistent disk. Crossplay ON; players join via PlayFab code.
- **Lavalink VM (`server/lavalink/` + `infra/modules/gcp-lavalink-vm`)** — single GCE `e2-small` in `us-central1-a` running the Lavalink jar directly under systemd (no Docker). Stateless — no persistent disk. Reuses the Valheim VPC. Exposes Lavalink's REST + WebSocket endpoints on TCP 2333; password lives in `lavalink-server-password` GSM secret. Bot connects via Wavelink, which speaks the v4 protocol.
- **Backups** — daily GCE disk snapshots + `gsutil rsync` of Valheim world files to a GCS bucket. Lavalink is stateless, no backups. *(Not yet built; will land as `infra/modules/gcp-backups`.)*
- **Idle watcher (`infra/modules/gcp-idle-watcher`)** — Cloud Scheduler → Cloud Function. Multi-target: polls Valheim's status HTTP endpoint (`/status.json`) and Lavalink's `/v4/players` REST endpoint, stops each VM independently after N consecutive empty checks. State is keyed per target (`state-valheim.json`, `state-lavalink.json`) in a single GCS bucket.

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

## Lavalink VM topology

```
                 ┌────────────────── Internet ──────────────────┐
                 │                                                │
                 │   TCP 2333 (Lavalink REST/WS, public; auth)   │
                 │   TCP 22  (only via IAP tunnel, 35.235.240.0/20)
                 │                                                │
       ┌─────────▼────────────────────────────────────────────────▼─────┐
       │                  valheim-vpc / valheim-subnet                  │
       │                  (shared with the Valheim VM)                  │
       │                                                                │
       │   ┌──────────────────────────────────────────────────────┐    │
       │   │  google_compute_instance.lavalink                     │    │
       │   │   • e2-small  • Debian 12 boot disk (10GB)            │    │
       │   │   • shielded VM    • ephemeral public IP              │    │
       │   │   • tag: lavalink-server                              │    │
       │   │   • SA: lavalink-vm-sa  (logging.logWriter +          │    │
       │   │     monitoring.metricWriter, secret-scoped reader)    │    │
       │   │                                                       │    │
       │   │   /opt/lavalink                                       │    │
       │   │     • Lavalink.jar (downloaded from GitHub releases)  │    │
       │   │     • application.yml (Spring env-substituted)        │    │
       │   │                                                       │    │
       │   │   systemd:                                            │    │
       │   │     lavalink-fetch-secrets.service (oneshot)          │    │
       │   │     lavalink.service (Requires=fetch-secrets)         │    │
       │   │       java -Xmx1G -Djava.net.preferIPv4Stack=true     │    │
       │   │            -jar /opt/lavalink/Lavalink.jar            │    │
       │   └──────────────────────────────────────────────────────┘    │
       │                            ▲                                   │
       │                            │  metadata server                  │
       │                            │  + SA token                       │
       │                            │                                   │
       └────────────────────────────┼───────────────────────────────────┘
                                    │
                       ┌────────────▼────────────────┐
                       │ Secret Manager              │
                       │   lavalink-server-password  │
                       └─────────────────────────────┘
```

The Lavalink VM has no persistent disk: state lives entirely in
Discord (active voice sessions) and the Lavalink jar caches; both are
disposable. `-Djava.net.preferIPv4Stack=true` avoids a JVM hang on
GCP egress where IPv6 is silently dropped. The youtube-source plugin
is bundled in via `application.yml` plugin coordinates so we're not
locked into Lavalink's stale built-in YouTube source.

## Bot ↔ Lavalink data flow

```
   /music play <q>     ┌─────────────┐
   ───────────────────►│ Discord     │
                        │ Gateway     │
                        └──────┬──────┘
                               │ INTERACTION_CREATE
                               ▼
                        ┌─────────────┐ ensure VM RUNNING ┌──────────────┐
                        │ bot         │──────────────────►│ Compute API  │
                        │ (music cog) │                   │ instances.*  │
                        └──────┬──────┘                   └──────────────┘
                               │ Wavelink (v4 WS+REST + voice forwarding)
                               ▼
                        ┌─────────────┐  YouTube/SC HTTPS    ┌──────────────┐
                        │ Lavalink    │─────────────────────►│ Audio source │
                        │ (GCE VM)    │                      └──────────────┘
                        └──────┬──────┘
                               │ Opus over Discord voice gateway
                               │ (DAVE/E2EE since Discord 2025 rollout)
                               ▼
                        ┌─────────────┐
                        │ Voice       │
                        │ channel     │
                        └─────────────┘
```

The bot's role is a thin orchestrator: handle the slash command, ensure the VM is up, ensure the Wavelink node is connected, hand the URL/query to Lavalink. Audio bytes never traverse the bot — they flow Lavalink → Discord directly. That's the whole reason Lavalink exists.

## Key interface boundaries

The bot exposes service modules with intentionally narrow public surfaces:

- [`bot/src/services/compute.py`](../bot/src/services/compute.py) — three free functions (`describe_instance`, `start_instance`, `stop_instance`) returning a frozen `InstanceState` dataclass. GCE-specific via `google-cloud-compute`. Used by both `/valheim *` and `/music *`.
- [`bot/src/services/server_query.py`](../bot/src/services/server_query.py) — one function `fetch_status(host, port)` returning a frozen `LiveStatus` dataclass. HTTP fetch from the Valheim VM's log-scraping daemon (`server/scripts/status-server.py`). Replaced an earlier Steam-A2S implementation that broke when Valheim's crossplay/PlayFab transport rolled out.
- [`bot/src/services/music.py`](../bot/src/services/music.py) — a thin Wavelink wrapper: `connect_node(client, host, port, password)` (idempotent, polls until CONNECTED), `play(voice_channel, query, requester_id=None)`, plus a few queue helpers. The cog calls these and adds no Discord-specific glue beyond what Wavelink already provides.
- [`bot/src/utils/checks.py`](../bot/src/utils/checks.py) — `requires_channel(setting_attr)` decorator factory. Pure-logic predicate so cogs can declaratively gate to a configured channel without bespoke per-command checks.

**Swapping clouds.** We deliberately picked a single concrete impl over an abstract `Protocol`/ABC up front. To swap to AWS/Hetzner/etc. later: rename `compute.py` → `gcp_compute.py`, lift the function signatures into a `Protocol` in a new `compute.py`, add an `aws_ec2.py` parallel impl, and update one import line per call site (currently `bot/src/cogs/valheim.py` and `bot/src/cogs/music.py`). The `InstanceState`/`GameState` dataclasses stay unchanged — providers map their native types into them.

The VM's runtime artifacts in `server/` are vendor-neutral on their own (only `fetch-secrets.sh` is GCP-specific, and there's one per VM type — both single files).

World-management surface (rewrite `world.env`, restart the service over SSH) is intentionally *not* in scope yet — it'll appear if/when we add `/valheim worlds switch <name>`.
