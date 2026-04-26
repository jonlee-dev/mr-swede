# Module: gcp-valheim-vm

Provisions a single Valheim dedicated server on Google Compute Engine,
including networking, persistent storage, secret plumbing, and the
cloud-init that bootstraps Docker + the lloesche/valheim-server image.

## What this module creates

| Resource                                | Purpose                                                       |
| --------------------------------------- | ------------------------------------------------------------- |
| `google_compute_network.valheim`        | Dedicated custom VPC, isolated from `default`                 |
| `google_compute_subnetwork.valheim`     | Single regional subnet (10.10.0.0/24)                         |
| `google_compute_firewall.iap_ssh`       | TCP 22 from Google's IAP range only (`35.235.240.0/20`)       |
| `google_compute_firewall.valheim_udp`   | UDP 2456-2458 from the public internet (target tag scoped)    |
| `google_service_account.valheim_vm`    | Identity attached to the VM                                   |
| `google_project_iam_member.vm_*`        | `logging.logWriter` + `monitoring.metricWriter` (project)     |
| `google_secret_manager_secret`          | Container for `valheim-server-password` (no version written)  |
| `google_secret_manager_secret_iam_member` | Secret-scoped `secretAccessor` for the VM SA                |
| `google_compute_disk.world_data`        | 20GB pd-balanced persistent disk, `prevent_destroy = true`    |
| `google_compute_instance.valheim`       | The VM, with `deletion_protection`, shielded VM, ephemeral IP |

## Inputs

See [`variables.tf`](variables.tf). The only required input is
`project_id`; everything else has a sensible default.

## Outputs

See [`outputs.tf`](outputs.tf). The bot consumes `instance_name`,
`instance_zone`, and `server_password_secret_id`; the backups module
consumes `world_data_disk_name` and `vm_service_account_email`.

## Post-apply steps

Terraform creates the secret container but **not** the value (the value
would otherwise land in plaintext in state). Seed it once:

```bash
PROJECT_ID="$(gcloud config get-value project)"
echo -n 'CHOOSE-A-PASSWORD' | \
  gcloud secrets versions add valheim-server-password \
    --project="$PROJECT_ID" --data-file=-
```

Rotate the same way -- a fresh `versions add` plus a service restart on
the VM picks up the new value (`fetch-secrets.sh` always reads `latest`).

## Why these design decisions

- **Custom VPC, not default.** The default VPC ships with broad
  `default-allow-*` firewall rules and applies them across every region.
  A dedicated VPC keeps blast radius small and the rule set legible.
- **No static IP.** PlayFab crossplay join codes mean players never see
  the IP, so paying $1.50/mo for a reserved address that changes nothing
  is wasteful.
- **Two-disk split.** Boot disk is ephemeral; world data lives on a
  separately-attached `pd-balanced` disk with `prevent_destroy`. Recovery
  from a corrupted boot disk is "detach data disk, reattach to a new VM."
- **Cloud-init via `templatefile()`.** The runtime artifacts in
  [`server/`](../../../server/) stay as standalone files (lintable,
  testable, AWS-portable). Terraform only concatenates them into a
  user-data blob at apply time.
- **`ignore_changes = [metadata.user-data]`.** Cloud-init runs once at
  first boot. If we re-rendered the user-data on every apply, Terraform
  would force VM replacement on every `server/` edit -- killing the
  point of the persistent disk.
- **Secret value not in Terraform.** The secret container is in TF; the
  value is not. State files leak too easily.

## Cost shape (us-central1, 24/7)

| Component             | Monthly cost (Apr 2026)  |
| --------------------- | ------------------------ |
| `e2-standard-2`       | ~$48                     |
| 10GB boot pd-balanced | ~$1                      |
| 20GB data pd-balanced | ~$2                      |
| Egress (PlayFab UDP)  | $0 inbound, ~$1 outbound |
| **Total 24/7**        | ~$52                     |

The bot stops the VM when idle. At ~3hr/day average usage the bill
drops to ~$8/mo (disks bill regardless of run state, ~$3/mo floor).
