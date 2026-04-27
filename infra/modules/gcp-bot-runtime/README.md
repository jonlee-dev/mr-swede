# Module: gcp-bot-runtime

Provisions everything needed to run the Mr. Swede Discord bot on Cloud
Run from a GitHub-hosted source: the runtime SA + IAM, the GSM secret
container, the Artifact Registry repo, the Cloud Run service itself,
and the Cloud Build trigger that builds and deploys it on push to
master.

## What this module creates

| Resource | Purpose |
|---|---|
| `google_service_account.bot` (`mr-swede-sa`) | Runtime identity for the Cloud Run service |
| `google_project_iam_member.bot_log_writer` | Cloud Logging from the runtime |
| `google_project_iam_member.bot_metric_writer` | Cloud Monitoring custom metrics (optional, cheap) |
| `google_secret_manager_secret.discord_bot_secrets` | Container for the Discord token JSON. **Imported** on first apply. |
| `google_secret_manager_secret_iam_member.bot_can_read_discord_secrets` | Secret-scoped accessor for the bot SA |
| `google_compute_instance_iam_member.bot_can_admin_valheim_vm` | Instance-scoped `compute.instanceAdmin.v1` (NOT project-wide) |
| `google_artifact_registry_repository.bot` | DOCKER repo at `<region>-docker.pkg.dev/<project>/<repo_id>` |
| `google_cloud_run_v2_service.bot` | The bot service. `template[0].containers[0].image` is `ignore_changes`d -- Cloud Build owns it. |
| `google_artifact_registry_repository_iam_member.build_can_push_images` | Cloud Build SA → AR writer |
| `google_project_iam_member.build_can_deploy_run` | Cloud Build SA → Cloud Run admin (project) |
| `google_service_account_iam_member.build_can_act_as_bot` | Cloud Build SA → ActAs the bot SA |
| `google_cloudbuild_trigger.bot_master` | Watches GitHub master, runs cloudbuild.yaml |

## Inputs

See [`variables.tf`](variables.tf). Required: `project_id`,
`valheim_instance_self_link`, `github_owner`. Everything else has a
sensible default.

## Outputs

See [`outputs.tf`](outputs.tf). The most useful ones for humans:
`service_url` (smoke test target), `discord_secret_path` (the value
shipped to the runtime as `DISCORD_SECRET_PATH`), `cloudbuild_trigger_id`
(for `gcloud builds triggers run`).

## Prerequisites

Two things must exist before `terraform apply` succeeds:

1. **GitHub ↔ Cloud Build connection.** A one-time GitHub App install
   in the GCP Console:
   _Cloud Build → Triggers → Connect Repository → GitHub (Cloud Build
   GitHub App) → authorize `jonlee-dev/mr-swede`._
   Without this, `google_cloudbuild_trigger.bot_master` fails on
   create.

2. **`discord-bot-secrets` exists in GSM** with a valid token already
   seeded. The bot was deployed via click-ops before TF; the secret
   pre-dates this module. **Do not let TF create a duplicate** -- import
   on first apply (see below).

## First-apply procedure

```bash
PROJECT_ID="$(gcloud config get-value project)"

cd infra/envs/prod

# 1. Plan once to confirm the only diff is the new module's resources.
terraform plan

# 2. Import the existing GSM secret BEFORE applying. Otherwise TF will
#    try to create a duplicate and crash with HTTP 409.
terraform import \
  module.bot_runtime.google_secret_manager_secret.discord_bot_secrets \
  "projects/${PROJECT_ID}/secrets/discord-bot-secrets"

# 3. Plan again. The discord_bot_secrets resource should now show
#    "no changes" (or only label drift). If it shows replacement,
#    inspect the replication block -- the imported secret may use
#    automatic replication while the module declares user_managed.
#    Edit secret.tf to match what's actually in GSM.
terraform plan

# 4. Apply.
terraform apply
```

After `apply`:

- Cloud Run service is created with the `cloudrun/hello` placeholder image.
- Push a commit to master (or run `gcloud builds triggers run mr-swede-master --branch=master`) to fire Cloud Build. The first build replaces the placeholder with the real bot image.
- Smoke test: `curl $(terraform output -raw bot_service_url)/health` should return `{"status": "starting", ...}` shortly after deploy and `{"status": "healthy", ...}` once the bot's gateway connection lands.

## Why these design decisions

- **Bootstrap with a public hello image.** `google_cloud_run_v2_service`
  refuses to create without an `image` field, but the AR repo is empty
  on first apply (Cloud Build hasn't run). Seeding with
  `cloudrun/hello` gets us past the chicken-and-egg.
- **`ignore_changes` on the image field.** After the first build, Cloud
  Build owns the image. Without `ignore_changes` every subsequent
  `terraform apply` would revert the running service to the placeholder.
- **Instance-scoped compute IAM.** `compute.instanceAdmin.v1` at the
  project level would let the bot start/stop GitHub Actions runners
  and any other VM in the project. Scoping to one instance shrinks
  blast radius for free.
- **Secret-scoped GSM accessor.** Same logic. The bot can read exactly
  one secret. If we ever add a second bot secret we add a second
  binding -- explicitness is the point.
- **Project-level `roles/run.admin` on Cloud Build SA.** Wider than
  ideal but Cloud Run lacks a clean service-scoped admin role for
  `services.update`. Custom-roles for one bot is over-engineering.
- **No `roles/datastore.user`, no `roles/run.invoker`.** The bot is
  stateless (Firestore was removed in v3.0.0) and doesn't call other
  services. Granting unused roles is a small but ongoing audit cost.
- **No `DISCORD_TOKEN` env var.** The current us-east4 service has the
  token as plaintext. Anyone with `run.services.getIamPolicy` on the
  service can read it. The new service reads via GSM only --
  `DISCORD_SECRET_PATH` points at the secret; the bot fetches at boot.
- **Cloud Build SA is the compute default SA.** As of GCP's 2024 default
  change, `<project-number>-compute@developer.gserviceaccount.com` runs
  Cloud Build by default unless overridden. If your project still uses
  the legacy `<project-number>@cloudbuild.gserviceaccount.com`, swap the
  email construction in `build_iam.tf`.

## Cost shape (us-central1, Apr 2026)

| Component | Monthly |
|---|---|
| Cloud Run, min=1 throttled, 512Mi/1vCPU | ~$3-5 |
| Artifact Registry, ~1GB image + churn | <$1 |
| Cloud Build, ~10 builds/month | $0 (under free tier) |
| Secret Manager, 2 active secret versions | <$0.06 |
| **Total bot runtime** | **~$4-6** |

The dominant ongoing cost across the whole project is still the
Valheim VM + disks; see [../gcp-valheim-vm/README.md](../gcp-valheim-vm/README.md).
