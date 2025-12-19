# Mr. Swede Discord Bot - TODO & Setup Guide

This document lists all the manual tasks needed to complete the setup of the Mr. Swede Discord bot for Cloud Run deployment.

## 🔐 Secrets Structure

Your secrets are stored as JSON in Google Secret Manager:

| Secret Name | Keys | Resource URI |
|-------------|------|--------------|
| `blizzard-secrets` | `client_id`, `client_secret` | `projects/749144818572/secrets/blizzard-secrets/versions/1` |
| `discord-bot-secrets` | `mr-swede.id`, `mr-swede.token`, `mr-swede.public_key`, `ow2-ranked-bot.id`, `ow2-ranked-bot.token`, `ow2-ranked-bot.public_key` | `projects/749144818572/secrets/discord-bot-secrets/versions/1` |
| `spotify-secrets` | `client_id`, `client_secret` | `projects/749144818572/secrets/spotify-secrets/versions/1` |

The bot automatically loads these secrets via the `SecretManager` class in `src/config/secrets.py`.

### Switching Between Discord Bots

To use a different Discord bot, set the `DISCORD_BOT_NAME` environment variable:

```bash
# Use mr-swede (default)
DISCORD_BOT_NAME=mr-swede

# Use ow2-ranked-bot
DISCORD_BOT_NAME=ow2-ranked-bot
```

---

## 🔧 Permissions & Access Required

### Google Cloud Platform (GCP)

#### Required APIs to Enable
```bash
# Enable required GCP APIs
gcloud services enable \
  secretmanager.googleapis.com \
  firestore.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  containerregistry.googleapis.com \
  cloudresourcemanager.googleapis.com
```

#### Service Account Setup
1. **Create a service account for Cloud Run:**
   ```bash
   gcloud iam service-accounts create mr-swede-sa \
     --display-name="Mr. Swede Discord Bot"
   ```

2. **Grant required roles to the service account:**
   ```bash
   PROJECT_ID=$(gcloud config get-value project)
   SA_EMAIL="mr-swede-sa@${PROJECT_ID}.iam.gserviceaccount.com"
   
   # Secret Manager access (to read JSON secrets)
   gcloud projects add-iam-policy-binding $PROJECT_ID \
     --member="serviceAccount:${SA_EMAIL}" \
     --role="roles/secretmanager.secretAccessor"
   
   # Firestore access
   gcloud projects add-iam-policy-binding $PROJECT_ID \
     --member="serviceAccount:${SA_EMAIL}" \
     --role="roles/datastore.user"
   
   # Cloud Run invoker (for health checks)
   gcloud projects add-iam-policy-binding $PROJECT_ID \
     --member="serviceAccount:${SA_EMAIL}" \
     --role="roles/run.invoker"
   ```

3. **Grant Cloud Build permissions:**
   ```bash
   # Get Cloud Build service account
   BUILD_SA="${PROJECT_ID}@cloudbuild.gserviceaccount.com"
   
   # Grant Cloud Run deployment permissions
   gcloud projects add-iam-policy-binding $PROJECT_ID \
     --member="serviceAccount:${BUILD_SA}" \
     --role="roles/run.admin"
   
   gcloud projects add-iam-policy-binding $PROJECT_ID \
     --member="serviceAccount:${BUILD_SA}" \
     --role="roles/iam.serviceAccountUser"
   ```

---

## 🎮 Discord Developer Portal Setup

### For mr-swede Bot
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Select or create your application
3. Go to "Bot" section
4. Under "Privileged Gateway Intents", enable:
   - [x] **PRESENCE INTENT** (optional, for richer status)
   - [x] **SERVER MEMBERS INTENT** (for member tracking)
   - [x] **MESSAGE CONTENT INTENT** (for legacy prefix commands)

### OAuth2 & Permissions
1. Go to "OAuth2" → "URL Generator"
2. Select scopes:
   - [x] `bot`
   - [x] `applications.commands`
3. Select bot permissions:
   - [x] Send Messages
   - [x] Embed Links
   - [x] Attach Files
   - [x] Read Message History
   - [x] Use External Emojis
   - [x] Connect (voice)
   - [x] Speak (voice)
   - [x] Use Voice Activity
4. Use generated URL to invite bot to your server

---

## 🎵 Spotify Developer Setup

Your Spotify secrets are already in GSM at `spotify-secrets` with:
- `client_id`
- `client_secret`

If you need to update them:
1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Select your app
3. Note the Client ID and Client Secret
4. Update GSM:
   ```bash
   echo '{"client_id": "your-id", "client_secret": "your-secret"}' | \
     gcloud secrets versions add spotify-secrets --data-file=-
   ```

---

## 🎯 Blizzard Developer Setup

Your Blizzard secrets are already in GSM at `blizzard-secrets` with:
- `client_id`
- `client_secret`

If you need to update them:
1. Go to [Blizzard Developer Portal](https://develop.battle.net/)
2. Select your client
3. Note the Client ID and Client Secret
4. Update GSM:
   ```bash
   echo '{"client_id": "your-id", "client_secret": "your-secret"}' | \
     gcloud secrets versions add blizzard-secrets --data-file=-
   ```

---

## 🗄️ Firestore Database Setup

### Create Firestore Database
```bash
# Create Firestore in Native mode (if not already created)
gcloud firestore databases create --location=us-central1
```

### Firestore Collections

The bot uses these collections (auto-created on first use):
- `mr_swede_accounts` - Tracked Overwatch accounts
- `mr_swede_stats_history` - Historical stats snapshots
- `mr_swede_user_preferences` - User settings

### Optional: Create Indexes for Better Performance
Create a file `firestore.indexes.json`:

```json
{
  "indexes": [
    {
      "collectionGroup": "mr_swede_accounts",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "discord_user_id", "order": "ASCENDING" },
        { "fieldPath": "created_at", "order": "DESCENDING" }
      ]
    },
    {
      "collectionGroup": "mr_swede_stats_history",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "account_id", "order": "ASCENDING" },
        { "fieldPath": "recorded_at", "order": "DESCENDING" }
      ]
    }
  ]
}
```

Deploy indexes:
```bash
gcloud firestore indexes create --file=firestore.indexes.json
```

---

## 🚀 Cloud Run Deployment

### First-Time Deployment
```bash
# Deploy using Cloud Build
gcloud builds submit --config=cloudbuild.yaml

# Or deploy directly with gcloud
gcloud run deploy mr-swede \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --min-instances 1 \
  --max-instances 3 \
  --memory 1Gi \
  --timeout 3600 \
  --set-env-vars "ENV=production,LOG_FORMAT=json,DISCORD_BOT_NAME=mr-swede" \
  --service-account mr-swede-sa@${PROJECT_ID}.iam.gserviceaccount.com
```

### Switch to ow2-ranked-bot
```bash
gcloud run deploy mr-swede \
  --set-env-vars "DISCORD_BOT_NAME=ow2-ranked-bot"
```

### Set Up Cloud Build Trigger (for CI/CD)
```bash
# Connect your GitHub repository first via Cloud Console, then:
gcloud builds triggers create github \
  --repo-name=mr-swede \
  --repo-owner=jonlee-dev \
  --branch-pattern="^main$" \
  --build-config=cloudbuild.yaml
```

---

## 💻 Local Development Setup

### Prerequisites
- Python 3.11+
- Poetry (dependency management)
- FFmpeg (for audio playback)
- `gcloud` CLI authenticated

### Install Dependencies
```bash
# Install Poetry (if not already installed)
curl -sSL https://install.python-poetry.org | python3 -

# Install project dependencies
poetry install

# Activate virtual environment
poetry shell
```

### Authentication for GSM
```bash
# Authenticate with GCP (one-time)
gcloud auth application-default login

# Verify authentication
gcloud secrets versions access latest --secret=discord-bot-secrets
```

### Run Locally
```bash
# Run with HTTP server (like Cloud Run)
poetry run python -m src.main

# Or run standalone bot (no HTTP server)
poetry run python -m src.main --standalone

# Use a different bot
DISCORD_BOT_NAME=ow2-ranked-bot poetry run python -m src.main --standalone
```

### Run Tests
```bash
# Run all tests
poetry run pytest

# Run only unit tests
poetry run pytest tests/unit -v

# Run with coverage
poetry run pytest --cov=src --cov-report=html

# Run acceptance tests
poetry run pytest tests/acceptance -v
```

---

## 📋 Post-Deployment Checklist

- [ ] Service account has `secretmanager.secretAccessor` role
- [ ] Service account has `datastore.user` role
- [ ] Discord bot is online and responding to `/ping`
- [ ] Slash commands are synced (try `/help`)
- [ ] Overwatch stats work (`/ow stats YourBattleTag#1234`)
- [ ] Music playback works (`/play song name`)
- [ ] Voice channel joining works
- [ ] Firestore database has collections created
- [ ] Cloud Run health check passes (`/health` endpoint)
- [ ] Logs are visible in Cloud Logging

---

## 🐛 Troubleshooting

### Common Issues

**"Discord secrets not found":**
- Check GSM secret name is exactly `discord-bot-secrets`
- Verify the JSON structure has keys like `mr-swede.token`
- Ensure service account has `secretmanager.secretAccessor` role

**Bot doesn't respond to commands:**
- Check `DISCORD_BOT_NAME` matches a key in `discord-bot-secrets`
- Verify bot has correct permissions in server
- Check Cloud Run logs for errors

**Voice features don't work:**
- Ensure FFmpeg is installed in Docker image
- Check `min-instances` is set to 1
- Verify bot has Connect and Speak permissions

**Overwatch stats not working:**
- Player profile might be private (must be public)
- BattleTag is case-sensitive
- Overfast API might be temporarily down

**"Blizzard credentials not found":**
- Check GSM secret `blizzard-secrets` exists
- Verify JSON has `client_id` and `client_secret` keys
- This is optional - bot works without Blizzard features

---

## 📝 Notes

### Cost Considerations
- **Cloud Run**: With `min-instances=1`, baseline cost ~$15-30/month
  - Set `min-instances=0` to scale to zero (but voice won't work reliably)
- **Firestore**: Free tier includes 1GB storage, 50k reads/day, 20k writes/day
- **Secret Manager**: First 6 active secrets free, then $0.03/version/month

### Voice Channel Limitations
- Cloud Run's stateless nature makes persistent voice connections challenging
- `min-instances=1` keeps one instance warm for voice
- Consider Cloud Run Jobs or Compute Engine for 24/7 voice if needed

---

## 🔄 Future Improvements

- [ ] Add Redis/Memorystore for caching API responses
- [ ] Implement playlist saving to Firestore
- [ ] Add scheduled stats refresh using Cloud Scheduler
- [ ] Create web dashboard for stats visualization
- [ ] Add support for more games (Valorant, etc.)
