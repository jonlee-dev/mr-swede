# Mr. Swede Discord Bot - TODO & Setup Guide

This document lists all the manual tasks needed to complete the setup of the Mr. Swede Discord bot for Cloud Run deployment.

## 🔐 Permissions & Access Required

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
   
   # Secret Manager access
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

## 🗝️ Secrets to Create in Google Secret Manager

Create the following secrets in GSM. The bot expects these exact names:

```bash
PROJECT_ID=$(gcloud config get-value project)

# Discord Bot Token
echo -n "your-discord-token" | gcloud secrets create discord-token \
  --data-file=- --replication-policy="automatic"

# Blizzard API credentials
echo -n "your-client-id" | gcloud secrets create blizzard-client-id \
  --data-file=- --replication-policy="automatic"

echo -n "your-client-secret" | gcloud secrets create blizzard-client-secret \
  --data-file=- --replication-policy="automatic"

# Spotify API credentials  
echo -n "your-client-id" | gcloud secrets create spotify-client-id \
  --data-file=- --replication-policy="automatic"

echo -n "your-client-secret" | gcloud secrets create spotify-client-secret \
  --data-file=- --replication-policy="automatic"
```

---

## 🎮 Discord Developer Portal Setup

### Create Discord Application
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" and name it "Mr. Swede"
3. Note the **Application ID** (needed for `DISCORD_APPLICATION_ID`)

### Bot Setup
1. Go to "Bot" section
2. Click "Add Bot"
3. Under "Privileged Gateway Intents", enable:
   - [x] **PRESENCE INTENT** (optional, for richer status)
   - [x] **SERVER MEMBERS INTENT** (for member tracking)
   - [x] **MESSAGE CONTENT INTENT** (for legacy prefix commands)
4. Copy the **Bot Token** (this goes in GSM as `discord-token`)

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
5. Note your **Guild ID** for faster command sync during development

---

## 🎵 Spotify Developer Setup

### Create Spotify Application
1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Click "Create App"
3. Fill in details:
   - App name: "Mr. Swede"
   - Description: "Discord bot music features"
   - Redirect URI: `http://localhost:8080/callback`
4. Note the **Client ID** and **Client Secret**

---

## 🎯 Blizzard Developer Setup

### Create Blizzard API Client
1. Go to [Blizzard Developer Portal](https://develop.battle.net/)
2. Click "Create Client"
3. Fill in details:
   - Client Name: "Mr. Swede Discord Bot"
   - Redirect URIs: `https://localhost/callback`
   - Intended Use: Personal/Hobby project
4. Note the **Client ID** and **Client Secret**

---

## 🗄️ Firestore Database Setup

### Create Firestore Database
```bash
# Create Firestore in Native mode
gcloud firestore databases create --location=us-central1
```

### Firestore Indexes (Optional, for better query performance)
Create composite indexes for common queries. Create a file `firestore.indexes.json`:

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
# Build and deploy using Cloud Build
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
  --set-secrets "DISCORD_TOKEN=discord-token:latest,BLIZZARD_CLIENT_ID=blizzard-client-id:latest,BLIZZARD_CLIENT_SECRET=blizzard-client-secret:latest,SPOTIFY_CLIENT_ID=spotify-client-id:latest,SPOTIFY_CLIENT_SECRET=spotify-client-secret:latest" \
  --service-account mr-swede-sa@${PROJECT_ID}.iam.gserviceaccount.com
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

### Install Dependencies
```bash
# Install Poetry (if not already installed)
curl -sSL https://install.python-poetry.org | python3 -

# Install project dependencies
poetry install

# Activate virtual environment
poetry shell
```

### Set Up Environment Variables
```bash
# Copy example env file
cp env.example .env

# Edit .env with your credentials
# For local dev, set USE_GSM=false
```

### Run Locally
```bash
# Run with HTTP server (like Cloud Run)
poetry run python -m src.main

# Or run standalone bot (no HTTP server)
poetry run python -m src.main --standalone
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

- [ ] Discord bot is online and responding to `/ping`
- [ ] Slash commands are synced (try `/help`)
- [ ] Overwatch stats work (`/ow stats YourBattleTag#1234`)
- [ ] Music playback works (`/play song name`)
- [ ] Voice channel joining works
- [ ] Firestore database has collections created
- [ ] Cloud Run health check passes (`/health` endpoint)
- [ ] Logs are visible in Cloud Logging
- [ ] Auto-scaling works (check Cloud Run metrics)

---

## 🐛 Troubleshooting

### Common Issues

**Bot doesn't respond to commands:**
- Check Discord bot token is correct in GSM
- Verify bot has correct permissions in server
- Check Cloud Run logs for errors

**Voice features don't work:**
- Ensure FFmpeg is installed in Docker image
- Check `min-instances` is set to 1 (voice needs persistent connection)
- Verify bot has Connect and Speak permissions

**Overwatch stats not working:**
- Player profile might be private (must be public)
- BattleTag is case-sensitive
- Overfast API might be temporarily down

**Secrets not loading:**
- Verify service account has `secretmanager.secretAccessor` role
- Check secret names match exactly
- Ensure secrets have at least one version

**Database errors:**
- Verify Firestore is created in Native mode
- Check service account has `datastore.user` role
- Ensure `FIRESTORE_COLLECTION_PREFIX` is set

---

## 📝 Notes

### Cost Considerations
- **Cloud Run**: With `min-instances=1`, you'll have some baseline cost (~$15-30/month)
  - Set `min-instances=0` to scale to zero (but voice won't work reliably)
- **Firestore**: Free tier includes 1GB storage, 50k reads/day, 20k writes/day
- **Secret Manager**: First 6 active secrets free, then $0.03/version/month
- **Container Registry**: Storage costs for Docker images

### Voice Channel Limitations
- Cloud Run's stateless nature makes persistent voice connections challenging
- `min-instances=1` keeps one instance warm for voice
- Consider Cloud Run Jobs or Compute Engine for 24/7 voice if needed

### Rate Limits
- **Overfast API**: Be respectful, add caching for frequent requests
- **Discord API**: Slash command syncing has rate limits (use guild sync for dev)
- **Spotify API**: Rate limits apply, use client credentials flow

---

## 🔄 Future Improvements

- [ ] Add Redis/Memorystore for caching API responses
- [ ] Implement playlist saving to Firestore
- [ ] Add scheduled stats refresh using Cloud Scheduler
- [ ] Create web dashboard for stats visualization
- [ ] Add support for more games (Valorant, etc.)
- [ ] Implement user authentication for web features

