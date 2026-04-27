# Mr. Swede — setup & TODO

The manual checklist for standing up Mr. Swede on a fresh GCP project. Everything that can be in Terraform is — see [`infra/`](infra/). This file covers the click-ops prerequisites and the cutover steps that don't fit into `terraform apply`.

For the Valheim VM specifically: [docs/bootstrap.md](docs/bootstrap.md) and [docs/runbook.md](docs/runbook.md).

---

## What's done, what's left

The bot is fully functional: `/valheim status|start|stop` is wired to GCE + a Steam A2S query. The infra is fully Terraform-managed across three modules: `gcp-bootstrap`, `gcp-valheim-vm`, `gcp-bot-runtime`.

What's still ahead:

- **us-east4 → us-central1 cutover.** The bot's running Cloud Run service is in us-east4 (pre-Terraform). The `gcp-bot-runtime` module creates a fresh greenfield service in us-central1. After the new service is healthy, delete the us-east4 one by hand.
- **Idle watcher.** Cloud Scheduler + Cloud Function that polls the VM's A2S port and stops the VM after N minutes of zero players. Not started.

---

## Secrets in GSM

| Secret | Created by | Value seeded by |
|---|---|---|
| `discord-bot-secrets` | TF module `gcp-bot-runtime` (imported on first apply — pre-existed click-ops deploy) | Out-of-band: `gcloud secrets versions add discord-bot-secrets --data-file=-` |
| `valheim-server-password` | TF module `gcp-valheim-vm` | Out-of-band: see [docs/bootstrap.md](docs/bootstrap.md) |

Both secrets follow the same pattern: Terraform owns the container and the IAM bindings, but the value is seeded out-of-band so the payload never enters TF state.

The `discord-bot-secrets` JSON layout (preferred, nested-object form):

```json
{
  "mr-swede": {
    "id": "123456789",
    "token": "your-bot-token",
    "public_key": "your-public-key"
  }
}
```

The bot also accepts dot-notation keys (`"mr-swede.token": "..."`) for backwards compatibility — see [bot/src/config/secrets.py](bot/src/config/secrets.py).

---

## First-time GCP setup

### 1. Run the bootstrap once

[docs/bootstrap.md](docs/bootstrap.md) walks through the one-shot Terraform bootstrap that turns on APIs, creates the state bucket, sets up Workload Identity Federation, and creates the `terraform-ci` SA. After it succeeds you don't need local `gcloud` for routine TF.

### 2. Connect Cloud Build to GitHub (one-time, manual)

The OAuth handshake that lets Cloud Build read this repo can't be done in Terraform — it's a GitHub App install. In the GCP console:

> **Cloud Build → Triggers → Connect Repository → GitHub (Cloud Build GitHub App) → authorize `jonlee-dev/mr-swede`**

After this is done, the `google_cloudbuild_trigger` in `gcp-bot-runtime` works on the next apply.

### 3. Apply `gcp-bot-runtime` (one-time)

The first apply needs an extra step: import the existing `discord-bot-secrets` GSM secret so TF doesn't try to re-create it.

```bash
PROJECT_ID="$(gcloud config get-value project)"
cd infra/envs/prod

terraform plan        # Sanity check: should see new resources for module.bot_runtime
terraform import \
  module.bot_runtime.google_secret_manager_secret.discord_bot_secrets \
  "projects/${PROJECT_ID}/secrets/discord-bot-secrets"
terraform plan        # discord_bot_secrets should now show "no changes" (or only label drift)
terraform apply
```

If the post-import plan shows a forced replacement on `discord_bot_secrets`, the live secret's replication block doesn't match what `infra/modules/gcp-bot-runtime/secret.tf` declares. Edit that file to match (most likely: change `user_managed { ... }` to `automatic {}`).

### 4. Trigger the first Cloud Build

```bash
gcloud builds triggers run mr-swede-master --branch=master
```

This replaces the `cloudrun/hello` placeholder image with the real bot. The first build takes ~5 minutes (multi-stage Docker + dependency install).

### 5. Smoke test

```bash
SERVICE_URL="$(terraform -chdir=infra/envs/prod output -raw bot_service_url)"
curl "${SERVICE_URL}/health"
# {"status": "starting", "bot_ready": false}     ← right after deploy
# {"status": "healthy", "bot_ready": true, ...}  ← once gateway connects
```

Then in Discord: `/ping`, `/info`, `/valheim status`.

### 6. Delete the old us-east4 service (manual)

```bash
gcloud run services delete mr-swede --region=us-east4
gcloud artifacts repositories delete cloud-run-source-deploy --location=us-east4 --quiet
```

These never lived in Terraform. Once the new us-central1 service is healthy, the old ones are dead weight.

---

## Discord developer portal

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. **Bot** → uncheck all privileged intents. The slash-only bot doesn't need MESSAGE CONTENT, SERVER MEMBERS, or PRESENCE.
3. **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `Send Messages`, `Embed Links`, `Use Slash Commands`
4. Use the generated URL to invite the bot to your server.

---

## Local development

```bash
cd bot
poetry install
gcloud auth application-default login   # for GSM lookups
poetry run python -m src.main
```

If you don't have GSM access, set `DISCORD_TOKEN` in `bot/.env` to skip GSM entirely.

---

## Post-cutover checklist

- [ ] Cloud Build trigger `mr-swede-master` exists and points at `master`
- [ ] AR repo `cloud-run-source-deploy` exists in us-central1 with at least one image
- [ ] Cloud Run service `mr-swede` (us-central1) responds 200 on `/health`
- [ ] Bot is online and responds to `/ping` in Discord
- [ ] `/info` shows version 3.x and the three Valheim subcommands
- [ ] `/valheim status` reports VM state correctly
- [ ] `/valheim start` succeeds (RUNNING within ~30s)
- [ ] `/valheim stop` succeeds (TERMINATED within ~30s)
- [ ] Logs visible in Cloud Logging with structured JSON
- [ ] Old us-east4 Cloud Run service deleted
- [ ] Old us-east4 AR repo deleted

---

## Troubleshooting

**Bot won't connect.** Check `/health` — `bot_ready: false` with a non-empty `error` field tells you exactly why. Most often: missing `DISCORD_TOKEN` env var locally, or `DISCORD_SECRET_PATH` doesn't point at a readable secret.

**Slash commands don't appear.** Either you set `DISCORD_GUILD_ID` to the wrong guild, or you're waiting on global propagation (~1hr after first sync). For dev, set `DISCORD_GUILD_ID` to your test server for instant sync.

**`/valheim start` fails with PermissionDenied.** The bot SA is missing `compute.instanceAdmin.v1` on the Valheim instance. Re-run `terraform apply` — the binding is in `infra/modules/gcp-bot-runtime/instance_iam.tf`.

**Cloud Build fails on first run with "Permission denied to push to AR repo".** The Cloud Build SA hasn't been granted `roles/artifactregistry.writer` on the new repo. The TF module grants this — make sure the apply succeeded fully.

**Secret access errors in logs.** Confirm the bot SA has the secret-scoped `secretmanager.secretAccessor` binding:

```bash
gcloud secrets get-iam-policy discord-bot-secrets \
  --filter="bindings.members:mr-swede-sa@*"
```
