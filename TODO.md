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
  artifactregistry.googleapis.com \
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

## 🎬 YouTube Cookies Setup (Optional)

YouTube blocks requests from cloud servers (bot detection). To enable music playback, you need to provide YouTube cookies from a logged-in browser session.

### Step 1: Export cookies from your browser

**Chrome:**
1. Install [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
2. Log into YouTube in Chrome
3. Go to youtube.com
4. Click the extension → "Export"
5. Save as `cookies.txt`

**Firefox:**
1. Install [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)
2. Same steps as Chrome

### Step 2: Upload cookies to Secret Manager

```bash
# Create the secret
gcloud secrets create youtube-cookie \
  --data-file=cookies.txt \
  --project=mr-swede

# Grant access to the service account
gcloud secrets add-iam-policy-binding youtube-cookie \
  --member="serviceAccount:mr-swede-sa@mr-swede.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" \
  --project=mr-swede
```

### Step 3: Restart Cloud Run

```bash
gcloud run services update mr-swede --region=us-east4 \
  --update-env-vars="RESTART=$(date +%s)"
```

### Updating cookies

When cookies expire (usually every few weeks), re-export and update:

```bash
gcloud secrets versions add youtube-cookie \
  --data-file=cookies.txt \
  --project=mr-swede
```

### ⚠️ Notes

- Cookies expire periodically - you'll need to re-export them
- This is in a gray area with YouTube's Terms of Service
- Without cookies, `/play` will show an error about bot detection

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

### Automatic Deployment (GitHub Integration)

Cloud Run is connected directly to your GitHub repo. When you push to `main`, it automatically:
1. Builds the Docker image from your `Dockerfile`
2. Deploys to Cloud Run

No `cloudbuild.yaml` needed — settings are configured in Cloud Run directly.

### Cost-Optimized Configuration

After the first deployment, apply these settings to reduce costs from ~$35/month to ~$3-5/month:

```bash
# Apply cost-optimized settings (run once)
gcloud run services update mr-swede \
  --region=us-central1 \
  --cpu-throttling \
  --cpu-boost \
  --memory=512Mi \
  --cpu=1 \
  --min-instances=1 \
  --max-instances=1 \
  --timeout=3600 \
  --set-env-vars="ENV=production,LOG_FORMAT=json,DISCORD_BOT_NAME=mr-swede"
```

| Setting | Value | Why |
|---------|-------|-----|
| `--cpu-throttling` | Enabled | Only pay for CPU when processing commands |
| `--cpu-boost` | Enabled | Faster cold starts |
| `--memory=512Mi` | 512 MB | Sufficient for bot + audio |
| `--cpu=1` | 1 vCPU | Handles audio streaming |
| `--min-instances=1` | 1 | Keeps Discord connection alive |
| `--max-instances=1` | 1 | No need to scale for personal server |

**Estimated cost: ~$3-5/month**

### Switch to ow2-ranked-bot
```bash
gcloud run services update mr-swede \
  --region=us-central1 \
  --set-env-vars="DISCORD_BOT_NAME=ow2-ranked-bot"
```

### Manual Deployment (if needed)
```bash
# Deploy from source (uses Dockerfile)
gcloud run deploy mr-swede \
  --source . \
  --region=us-central1 \
  --allow-unauthenticated \
  --service-account=mr-swede-sa@${PROJECT_ID}.iam.gserviceaccount.com
```

---

## 💻 Local Development Setup

### Prerequisites
- Python 3.12+
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
- **Cloud Run**: With CPU throttling + `min-instances=1`, cost is ~$3-5/month
  - Without throttling: ~$35/month (not recommended)
  - With `min-instances=0`: ~$0 but voice connections will drop
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
