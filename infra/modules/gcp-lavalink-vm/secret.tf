###############################################################################
# Lavalink server password secret.
#
# Same out-of-band-seeding pattern as gcp-valheim-vm/secret.tf:
# Terraform owns the SECRET (the named container) and the IAM binding,
# but NOT the value. Adding a google_secret_manager_secret_version
# with the password as input would put the password into Terraform
# state in plaintext.
#
# After `terraform apply` the user seeds the value once with:
#
#   gcloud secrets versions add lavalink-server-password \
#     --project=$PROJECT_ID --data-file=- \
#     <<<"$(openssl rand -hex 32)"
#
# Rotation is the same command with a new value. The new password
# takes effect on next VM boot via fetch-secrets.sh.
#
# The bot and idle-watcher each get their own secret-scoped reader
# binding from their respective modules (gcp-bot-runtime,
# gcp-idle-watcher) -- this module owns the container + grants the
# VM SA itself. Adding more readers means adding more
# google_secret_manager_secret_iam_member resources elsewhere, NOT
# expanding this module.
###############################################################################

resource "google_secret_manager_secret" "server_password" {
  project   = var.project_id
  secret_id = "lavalink-server-password"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = var.labels
}

# Secret-scoped access: the VM SA can read THIS secret only.
resource "google_secret_manager_secret_iam_member" "vm_can_read_password" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.server_password.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.lavalink_vm.email}"
}

###############################################################################
# Spotify Developer credentials -- consumed by the lavasrc plugin running
# inside Lavalink to resolve Spotify URLs (tracks, playlists, albums) into
# YouTube-source-backed Playables.
#
# Single JSON-shaped secret rather than two split secrets:
#   {"client_id": "...", "client_secret": "..."}
#
# Why one secret with JSON, not two:
#   - One IAM binding to manage instead of two
#   - Atomic rotation: you can never end up with a new id paired with
#     an old secret (the failure mode of two-secret schemes)
#   - Mirrors the discord-bot-secrets shape used by the bot
#
# fetch-secrets.sh on the VM parses this with `jq` into two env vars
# (PLUGINS_LAVASRC_SOURCES_SPOTIFY_CLIENT_ID, *_CLIENT_SECRET) which
# Spring Boot env-substitutes into application.yml at JVM start.
#
# Same out-of-band-seeding pattern: Terraform owns the container, NOT
# the value. After `terraform apply`, register a Spotify Developer
# App at https://developer.spotify.com/dashboard and seed once with:
#
#   echo -n '{"client_id":"...","client_secret":"..."}' | \
#     gcloud secrets versions add spotify-client-credentials \
#       --project=$PROJECT_ID --data-file=-
###############################################################################

resource "google_secret_manager_secret" "spotify_credentials" {
  project   = var.project_id
  secret_id = var.spotify_credentials_secret_id

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = var.labels
}

resource "google_secret_manager_secret_iam_member" "vm_can_read_spotify_credentials" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.spotify_credentials.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.lavalink_vm.email}"
}
