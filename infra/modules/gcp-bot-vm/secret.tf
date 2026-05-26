###############################################################################
# Lavalink YouTube OAuth refresh token.
#
# Stores a long-lived Google OAuth refresh token that the youtube-source
# plugin uses to authenticate as a real user when hitting YouTube,
# bypassing the periodic anti-bot rollouts that take out unauthenticated
# clients (the 2026-05-25 incident shape: "This video requires login").
#
# Out-of-band seeding pattern, same as lavalink-server-password and
# spotify-client-credentials:
#
#   1. terraform apply creates the SECRET (container), no value.
#   2. On the bot-vm, Lavalink boots without a token, enters the
#      device-code flow, prints a URL + code to its journal.
#   3. Operator opens the URL on a phone/laptop signed into a BURNER
#      Google account (NOT primary -- account may get flagged), enters
#      the code.
#   4. Plugin captures the refresh token, prints it to the journal.
#   5. Operator extracts the token from the journal and seeds it:
#
#        gcloud secrets versions add lavalink-youtube-oauth-token \
#          --project=$PROJECT_ID --data-file=- <<<"$REFRESH_TOKEN"
#
#   6. Restart lavalink. From now on, fetch-secrets pulls the token at
#      every boot, Lavalink uses it silently, no human in the loop.
#
# See docs/runbook.md scenario 20 for the operator-facing instructions
# and recovery procedure when the burner account gets terminated.
#
# IAM binding lives in iam.tf (alongside other bot-vm secret bindings)
# rather than here so the "which secrets does the bot-vm SA read?"
# question has one answer file.
#
# Placement note: lavalink-server-password + spotify-client-credentials
# are still owned by the (retired) gcp-lavalink-vm module. The OAuth
# secret lives here because it's a new addition AND because long-term
# all three should move into gcp-bot-vm before gcp-lavalink-vm is
# destroyed. See PRD decision log 2026-05-25.
###############################################################################

resource "google_secret_manager_secret" "youtube_oauth_token" {
  project   = var.project_id
  secret_id = "lavalink-youtube-oauth-token"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = var.labels
}

resource "google_secret_manager_secret_iam_member" "bot_vm_can_read_youtube_oauth_token" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.youtube_oauth_token.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.service_account_email}"
}
