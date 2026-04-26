# Architecture

## Components

- **Bot (`bot/`)** — Python + discord.py (Gateway), deployed to Cloud Run with `min-instances=1`. Slash-only. Handles `/ping`, `/info`, and the `/valheim status|start|stop` group. *(Phase 3 wires `/valheim *` to the VM; the rest already work.)*
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

The bot exposes two service modules with intentionally narrow public surfaces:

- [`bot/src/services/compute.py`](../bot/src/services/compute.py) — three free functions (`describe_instance`, `start_instance`, `stop_instance`) returning a frozen `InstanceState` dataclass. GCE-specific today; Phase 3 will fill the stubs using `google-cloud-compute`.
- [`bot/src/services/server_query.py`](../bot/src/services/server_query.py) — one function `query(host, port)` returning a frozen `GameState` dataclass. Steam A2S today; Phase 3 will fill the stub using `python-a2s`.

**Swapping clouds.** We deliberately picked a single concrete impl over an abstract `Protocol`/ABC up front. To swap to AWS/Hetzner/etc. later: rename `compute.py` → `gcp_compute.py`, lift the function signatures into a `Protocol` in a new `compute.py`, add an `aws_ec2.py` parallel impl, and update one import line per call site (currently just [`bot/src/cogs/valheim.py`](../bot/src/cogs/valheim.py)). The `InstanceState`/`GameState` dataclasses stay unchanged — providers map their native types into them.

The VM's runtime artifacts in `server/` are vendor-neutral on their own (only `fetch-secrets.sh` is GCP-specific, and it's a single file).

World-management surface (rewrite `world.env`, restart the service over SSH) is intentionally *not* in scope for Phase 3 — it'll appear if/when we add `/valheim worlds switch <name>`.
