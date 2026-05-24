# Module: gcp-lavalink-vm — RETIRED 2026-05-12

> **Status:** retained as a rollback path only. Lavalink now co-tenants
> the bot's VM via [`gcp-bot-vm`](../gcp-bot-vm/), running on
> `localhost:2333` alongside `bot.service`. The standalone VM this
> module provisions exists but doesn't serve traffic. Will be
> destroyed after the bot-vm soak; until then it's the one-flip
> rollback if the co-tenant setup misbehaves.
>
> The `server/lavalink/` artifacts (application.yml, fetch-secrets,
> systemd units) are STILL the source of truth for Lavalink config
> — `gcp-bot-vm` reads them too. Treat that directory as the
> Lavalink-config home; treat this module as the resource shell.

Provisions a Lavalink audio server on Google Compute Engine. Mirrors
`gcp-valheim-vm`'s shape so ops procedures translate directly: same
startup-script bootstrap, same secret-fetch-on-boot pattern, same
custom-role + instance-scoped IAM model.

## What this module creates

| Resource | Purpose |
|---|---|
| `google_service_account.lavalink_vm` (`lavalink-vm-sa`) | Identity attached to the VM |
| `google_project_iam_member.vm_log_writer` / `vm_metric_writer` | Logging + monitoring |
| `google_secret_manager_secret.server_password` | Container for `lavalink-server-password` (no version written) |
| `google_secret_manager_secret_iam_member.vm_can_read_password` | Secret-scoped read for the VM SA |
| `google_compute_firewall.lavalink_ingress` | TCP 2333 from anywhere (auth-protected by Lavalink) |
| `google_compute_firewall.lavalink_iap_ssh` | IAP SSH for the `lavalink-server` tag |
| `google_compute_instance.lavalink` | The VM (e2-small, shielded, ephemeral IP, joins shared VPC) |

No persistent data disk -- Lavalink is stateless from the VM's
perspective. Plugin downloads happen on first container boot; queue
state lives in the bot.

## Inputs

See [`variables.tf`](variables.tf). Required: `project_id`,
`vpc_self_link`, `subnet_self_link` (the latter two come from
`module.valheim_vm` outputs in the prod env). Everything else has a
sensible default.

## Outputs

See [`outputs.tf`](outputs.tf). The bot consumes `instance_name`,
`instance_zone`, `instance_self_link`, `lavalink_port`, and
`server_password_secret_id`. The idle-watcher consumes
`instance_self_link` for its own instance-scoped binding.

## Post-apply steps

Terraform creates the secret container but **not** the value (the
value would otherwise land in plaintext in TF state). Seed it once
with a random 32+ char password:

```bash
PROJECT_ID="$(gcloud config get-value project)"
gcloud secrets versions add lavalink-server-password \
  --project="$PROJECT_ID" --data-file=- <<<"$(openssl rand -hex 32)"
```

Rotation is the same command with a new value -- a fresh
`versions add` plus `gcloud compute instances reset lavalink-server`
picks up the new password (`fetch-secrets.sh` always reads `latest`
on boot).

## Why these design decisions

- **Shared VPC with Valheim VM.** Cheaper than a second custom VPC
  for one extra instance, and the security boundary doesn't matter
  for this small setup. If Lavalink ever needs network isolation
  from Valheim, factor a `gcp-network` module that both consume.
- **No persistent disk.** Lavalink's plugin cache rebuilds in seconds
  from Maven on cold start; queue state lives in the bot. Saving the
  cost of a persistent disk + the operational complexity of mount
  ordering is the right call for a stateless service.
- **e2-small default.** 2 vCPU shared, 2GB RAM -- plenty for 1-3
  concurrent voice channels at hobby load. The audio Opus encoding
  is the CPU-heavy part; e2-small handles it. Bump to `e2-medium`
  (1-2 vCPU dedicated, 4GB) via `var.machine_type` if audio glitches
  appear under load.
- **TCP 2333 open to 0.0.0.0/0.** Lavalink's REST + WebSocket layer
  requires a Bearer-style password header on every request. Without
  the header, all endpoints return 401 immediately. Treating the
  password header as the security boundary (rather than IP allow-
  listing) avoids tying us to Cloud Run's dynamic egress IP pool.
- **Custom-role IAM ownership.** This module does NOT create the
  IAM binding that lets the bot start/stop the VM. That binding
  lives in `gcp-bot-runtime` (and another in `gcp-idle-watcher`),
  both of which consume `instance_self_link` from this module's
  outputs. Each consumer owns its own grant -- principle of least
  privilege at the resource level.

## Cost shape (us-central1, Apr 2026)

| Component             | Monthly when running     |
| --------------------- | ------------------------ |
| `e2-small`            | ~$13                     |
| 10GB pd-balanced boot | ~$1                      |
| Egress (audio frames) | $0 inbound, ~$1 outbound |
| **Total 24/7**        | ~$15                     |

The idle-watcher stops the VM after the bot has been silent for
~5 min + the voice channel is empty. At ~1 hour/day average usage
the bill drops to ~$2-3/mo.
