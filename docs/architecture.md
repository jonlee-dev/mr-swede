# Architecture

## Components

- **Bot (`bot/`)** — Python + discord.py (Gateway), deployed to Cloud Run with `min-instances=1`. Handles `/valheim *` slash commands plus the existing music / Overwatch cogs. *(Phase 3 wires the new commands.)*
- **Valheim VM (`server/` + `infra/modules/gcp-valheim-vm`)** — single GCE `e2-standard-2` in `us-central1-a` running Docker Compose + `lloesche/valheim-server`, world data on a separately-attached `pd-balanced` persistent disk. Crossplay ON; players join via PlayFab code. *(Built in Phase 1.)*
- **Backups (`infra/modules/gcp-backups`)** — daily GCE disk snapshots + `gsutil rsync` of world files to a GCS bucket. *(Phase 2.)*
- **Idle watcher (`infra/modules/gcp-idle-watcher`)** — Cloud Scheduler → Cloud Function polling the VM's Steam A2S query port. Stops the VM after 30 min of zero players. *(Phase 7.)*

## Phase 1 — Valheim VM (current)

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

The cloud-init blob is rendered by `templatefile()` with the four runtime artifacts (`server/docker-compose.yml`, `server/scripts/*`) inlined as base64. Re-rendering triggers no VM replacement — `lifecycle.ignore_changes` deliberately drops `metadata.user-data` so the persistent disk survives `server/` edits.

## Key interface boundaries (Phase 3+)

- `bot/src/services/valheim/compute/` — abstract `ComputeProvider` with a `gcp.py` impl.
- `bot/src/services/valheim/world/` — abstract `WorldStorage` over SSH (rewrites `/etc/valheim/world.env` and triggers a service restart).
- `bot/src/services/valheim/query/` — abstract `ServerQuery` (Steam A2S + log tail for the PlayFab join code).

Swapping clouds means writing new implementations of these three interfaces. The command layer above is cloud-agnostic; the VM's runtime artifacts in `server/` are also vendor-neutral (only `fetch-secrets.sh` is GCP-specific, and it's a single file).
