# Bot Runtime — Terraform Design Notes

Context for an AI coding agent that will implement an `infra/modules/gcp-bot-runtime/`
module to codify the Cloud Run bot deployment. This document captures the
design boundaries, the decisions still to be made, and the things that
intentionally stay outside Terraform.

## Goal

Codify, in Terraform, every GCP resource that supports the `mr-swede` Discord bot
on Cloud Run, so that the deployment can be torn down and rebuilt by `terraform
apply` alone (modulo the manual prerequisites listed at the bottom).

The module should match the style and depth of the existing `gcp-valheim-vm`
module: narrow inputs, hide all internal wiring, document the *why* behind
non-obvious choices.

## Existing repo state (do not duplicate)

- `infra/modules/gcp-bootstrap/` — APIs enabled (incl. `run`, `cloudbuild`,
  `artifactregistry`, `secretmanager`), GCS state bucket, Workload Identity
  Federation pool/provider, `terraform-ci` SA with `editor` +
  `iam.serviceAccountAdmin` + `resourcemanager.projectIamAdmin`.
- `infra/modules/gcp-valheim-vm/` — VM (`valheim-server` in `us-central1-a`),
  data disk, VPC, IAP-tunneled SSH firewall, `valheim-vm-sa` runtime SA,
  `valheim-server-password` GSM secret container (value seeded out-of-band).
- `infra/envs/prod/main.tf` — wires bootstrap + valheim_vm modules, surfaces
  outputs (`valheim_instance_name`, `valheim_instance_zone`, etc.).
- `cloudbuild.yaml` at repo root — three steps (Build, Push, Deploy via
  `gcloud run services update`). Substitutions default to us-east4 today and
  must be flipped to us-central1.

The Cloud Run service currently lives in **us-east4**. Target is **us-central1**
(matches every other resource and the README). All TF below assumes a fresh
greenfield deploy in us-central1; the us-east4 service will be deleted manually
after cutover.

## Module shape: `infra/modules/gcp-bot-runtime/`

| File | Resources |
|---|---|
| `sa.tf` | `google_service_account "bot"` (`mr-swede-sa`), project IAM bindings: `roles/datastore.user`, `roles/run.invoker`. Plus instance-scoped `roles/compute.instanceAdmin.v1` on the Valheim VM (see decision #2). |
| `secret.tf` | `google_secret_manager_secret "discord_bot_secrets"` (replication = us-central1), `google_secret_manager_secret_iam_member` granting `secretmanager.secretAccessor` to the bot SA. **Value not managed by TF** — same pattern as `valheim-server-password`. |
| `registry.tf` | `google_artifact_registry_repository "cloud_run_source_deploy"` in us-central1, format = DOCKER. |
| `service.tf` | `google_cloud_run_v2_service "mr-swede"`. Service account = bot SA. Env vars from README (`ENV`, `LOG_FORMAT`, `DISCORD_BOT_NAME`, `DISCORD_GUILD_ID`, `VALHEIM_INSTANCE_NAME`, `VALHEIM_ZONE`, `GCP_PROJECT_ID`, `DISCORD_SECRET_PATH`). **No `DISCORD_TOKEN` env var** (read from GSM at runtime). `lifecycle.ignore_changes = [template[0].containers[0].image]` so Cloud Build can rotate the image without TF reverting it. |
| `build_iam.tf` | Cloud Build default SA (`<project-number>-compute@developer.gserviceaccount.com`, or whichever is configured): `roles/run.admin` (project), `roles/iam.serviceAccountUser` on the bot SA (so Cloud Build can deploy a service that runs as it), `roles/artifactregistry.writer` on the AR repo. |
| `trigger.tf` | `google_cloudbuild_trigger` watching `master` on the GitHub repo, `filename = "cloudbuild.yaml"`, substitutions setting `_AR_HOSTNAME=us-central1-docker.pkg.dev`, `_DEPLOY_REGION=us-central1`, `_AR_REPOSITORY=cloud-run-source-deploy`, `_SERVICE_NAME=mr-swede`. |
| `variables.tf` | `project_id`, `region` (default `us-central1`), `valheim_instance_self_link` (from `gcp-valheim-vm` output), `github_owner`, `github_repo`, `discord_guild_id`, `discord_bot_name` (default `mr-swede`). |
| `outputs.tf` | `service_url`, `service_account_email`, `artifact_registry_repository`, `discord_bot_secrets_secret_id`. |

Wire it into `infra/envs/prod/main.tf` after `module.valheim_vm`, passing
`valheim_instance_self_link = module.valheim_vm.instance_self_link`.

## Design decisions (need confirmation before code is written)

### 1. Image bootstrap chicken-and-egg

`google_cloud_run_v2_service` requires an `image` field on creation. On the
first `terraform apply` no image exists in the AR repo yet (Cloud Build hasn't
run), so creation will fail.

**Recommendation:** seed the service with a public placeholder image —
`us-docker.pkg.dev/cloudrun/container/hello` — and rely on
`ignore_changes = [template[0].containers[0].image]` so Cloud Build's first
push silently replaces it.

**Alternative:** trigger Cloud Build manually first, then apply. More steps,
but no placeholder lingering in state.

Going with the placeholder approach unless directed otherwise.

### 2. `compute.instanceAdmin.v1` scope

GCP's default would be project-level. The bot only needs to start/stop the one
Valheim VM, so we should bind at the **instance level** with
`google_compute_instance_iam_member` against
`module.valheim_vm.instance_self_link`. Tighter blast radius, costs nothing.

Going with instance-level binding.

### 3. Existing GSM secret adoption

The `discord-bot-secrets` GSM secret already exists and holds the bot token.
TF will create a new resource declaration; on first apply we either:

- **Import** the existing secret: `terraform import
  module.bot_runtime.google_secret_manager_secret.discord_bot_secrets
  projects/$PROJECT/secrets/discord-bot-secrets` — preserves the value, no
  rotation needed.
- **Recreate**: would require re-seeding the value. Not destructive but
  pointless work.

Going with import.

### 4. GitHub ↔ Cloud Build connection (manual prerequisite)

The OAuth handshake that lets Cloud Build read the GitHub repo cannot be
created in Terraform — it requires a one-time GitHub App install. Either:

- **Legacy GitHub trigger** (1st-gen): install the "Google Cloud Build" GitHub
  App once via console, then `google_cloudbuild_trigger` with `github { ... }`
  block works directly.
- **2nd-gen GitHub connection** (`google_cloudbuildv2_connection` +
  `google_cloudbuildv2_repository`): more TF surface area, but still requires
  the OAuth token to be created out-of-band and stored in Secret Manager.

Recommend 1st-gen for simplicity given this is a single-repo project.

### 5. Out-of-scope (intentionally NOT in Terraform)

- **Discord bot token rotation** — manual in Discord developer portal, then
  `gcloud secrets versions add discord-bot-secrets --data-file=-`.
- **Container image SHA** — owned by Cloud Build; the `image` field is
  `ignore_changes`d.
- **Secret payloads** — `discord-bot-secrets` content stays out of TF state,
  same rule as `valheim-server-password`.

## Security findings to surface

1. **Active token leak.** The current us-east4 Cloud Run service has
   `DISCORD_TOKEN` set as a plain environment variable (visible to anyone with
   `run.services.getIamPolicy` on the service). This token must be rotated in
   the Discord developer portal before cutover. The new TF-managed service
   reads from GSM only.

2. **Stale env var.** `DISCORD_OWNER_ID` is still set on the running service
   but unused since v3.0.0 dropped owner-only commands. Drop it on the new
   service.

3. **IAM gap.** The bot SA does not currently hold
   `roles/compute.instanceAdmin.v1` (or any compute role). `/valheim
   start|stop` will fail with PermissionDenied until this binding lands. The
   TF module fixes this.

## Migration order

1. **You:** rotate Discord token in dev portal; seed new value into GSM
   `discord-bot-secrets`.
2. Write the `gcp-bot-runtime` module.
3. Update `cloudbuild.yaml` substitutions: `_AR_HOSTNAME` →
   `us-central1-docker.pkg.dev`, `_DEPLOY_REGION` → `us-central1`.
4. Wire the module into `infra/envs/prod/main.tf`.
5. `terraform import` the existing GSM secret container.
6. `terraform plan` and review.
7. `terraform apply` (creates AR repo, SA + IAM, Cloud Run service with
   placeholder image, Cloud Build trigger).
8. Trigger a Cloud Build run on master (push or manual) — replaces placeholder
   image with the real bot.
9. Smoke test `/valheim status` against the new service URL.
10. Manually delete the us-east4 service: `gcloud run services delete mr-swede
    --region=us-east4`. (Out of TF scope — never lived in TF.)
11. Manually delete the us-east4 AR repo if no longer needed.

## Reference: README env var contract

```
ENV=production
LOG_FORMAT=json
DISCORD_BOT_NAME=mr-swede
DISCORD_GUILD_ID=<guild-id>
VALHEIM_INSTANCE_NAME=valheim-server
VALHEIM_ZONE=us-central1-a
GCP_PROJECT_ID=mr-swede
DISCORD_SECRET_PATH=projects/<project-number>/secrets/discord-bot-secrets/versions/latest
```

`DISCORD_TOKEN` deliberately absent — bot fetches via GSM using
`DISCORD_SECRET_PATH`.
