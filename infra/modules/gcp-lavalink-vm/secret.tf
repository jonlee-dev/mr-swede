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
