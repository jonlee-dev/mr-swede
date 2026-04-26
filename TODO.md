# Mr. Swede - setup & TODO

This is the manual-step checklist for standing up the Discord bot and the GCP resources it depends on. Code-level tasks live as comments in `bot/src/cogs/valheim.py` and `bot/src/services/compute.py`.

For the Valheim VM infrastructure (Terraform, cloud-init, secrets), see [docs/bootstrap.md](docs/bootstrap.md). For day-to-day operations, see [docs/runbook.md](docs/runbook.md).

---

## Phase status

| Phase | What | Status |
|---|---|---|
| 0 | Repo reorg into `bot/` + `infra/` | ✅ done |
| 0.5 | One-time Terraform bootstrap (WIF + state bucket) | ✅ done |
| 1 | Valheim VM + cloud-init + Secret Manager (Terraform) | ✅ done |
| 2 | Bot prune (kill OW/music/Firestore) + scaffolds for Phase 3 | ✅ done |
| 3 | Wire `/valheim status\|start\|stop` to GCE + A2S | ✅ done |
| 7 | Idle watcher (Cloud Function or scheduled job) | ⏳ next |

---

## Secrets in GSM

| Secret | Keys | Purpose |
|---|---|---|
| `discord-bot-secrets` | `mr-swede.id`, `mr-swede.token`, `mr-swede.public_key` | Discord bot token |
| `valheim-server-password` | _(no keys, just a payload)_ | Game server password — seeded out-of-band |

The `discord-bot-secrets` value is JSON; the `mr-swede` key holds the credentials for this bot. The `SecretManager` class in [bot/src/config/secrets.py](bot/src/config/secrets.py) reads it with both nested and dot-notation lookups.

The `valheim-server-password` secret container is created by Terraform but its value is seeded out-of-band — never put it in Terraform state. See [docs/bootstrap.md](docs/bootstrap.md) for the seeding command.

---

## GCP setup

### APIs to enable

```bash
gcloud services enable \
  secretmanager.googleapis.com \
  compute.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudresourcemanager.googleapis.com \
  iamcredentials.googleapis.com
```

### Service account for the Cloud Run bot

```bash
PROJECT_ID=$(gcloud config get-value project)
SA_EMAIL="mr-swede-sa@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts create mr-swede-sa \
  --display-name="Mr. Swede Discord Bot"

# Read Discord token from GSM
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"

# Phase 3: control the Valheim VM
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/compute.instanceAdmin.v1"
```

The `compute.instanceAdmin.v1` binding is wider than what we need for start/stop alone. When Phase 3 lands, narrow this to a custom role with just `compute.instances.start`, `compute.instances.stop`, `compute.instances.get`, and `compute.zoneOperations.get`.

---

## Discord developer portal

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Application → **Bot** → uncheck all privileged intents. The slash-only bot doesn't need MESSAGE CONTENT, SERVER MEMBERS, or PRESENCE.
3. Application → **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `Send Messages`, `Embed Links`, `Use Slash Commands`
4. Use the generated URL to invite the bot to your server.

---

## Cloud Run deployment (after Phase 2)

```bash
gcloud run deploy mr-swede \
  --source bot/ \
  --region=us-central1 \
  --service-account=mr-swede-sa@${PROJECT_ID}.iam.gserviceaccount.com \
  --cpu-throttling --cpu-boost \
  --memory=512Mi --cpu=1 \
  --min-instances=1 --max-instances=1 \
  --timeout=3600 \
  --set-env-vars="ENV=production,LOG_FORMAT=json,DISCORD_BOT_NAME=mr-swede,VALHEIM_INSTANCE_NAME=valheim-server,VALHEIM_ZONE=us-central1-a"
```

`min-instances=1` is required — Discord drops gateway sessions that go idle. CPU throttling keeps the warm-instance bill at ~$3-5/month.

---

## Local development

All `poetry`, `pytest`, and `python -m src.main` commands run from `bot/`.

```bash
cd bot
poetry install
gcloud auth application-default login   # for GSM lookups
poetry run python -m src.main
```

If you don't have GSM access, set `DISCORD_TOKEN` in `bot/.env` to skip GSM entirely.

---

## Post-deployment checklist

- [ ] Bot service account has `secretmanager.secretAccessor`
- [ ] Bot service account has `compute.instanceAdmin.v1` (or narrower equivalent — see above)
- [ ] Bot is online and responds to `/ping`
- [ ] `/info` shows the right version
- [ ] Cloud Run `/health` returns `{"status": "healthy", "bot_ready": true}`
- [ ] Logs visible in Cloud Logging with structured JSON
- [ ] (After Phase 3) `/valheim status` reports VM state correctly

---

## Troubleshooting

**Bot won't connect.** Check `/health` — `bot_ready: false` with a non-empty `error` field tells you exactly why. Most often: missing `DISCORD_TOKEN` env var or GSM permissions.

**Slash commands don't appear.** Either you set `DISCORD_GUILD_ID` to the wrong guild, or you're waiting on global propagation (~1hr after first sync). Set `DISCORD_GUILD_ID` to your test server for instant sync during dev.

**`/valheim status` says "not implemented yet".** That's intentional — Phase 2 left these as scaffolds. Phase 3 wires them up.

**GCS permissions errors.** Almost always the bot's service account is missing `secretmanager.secretAccessor` on `discord-bot-secrets` or `compute.instanceAdmin.v1` on the VM project. Check with:

```bash
gcloud projects get-iam-policy $PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:mr-swede-sa@*"
```
