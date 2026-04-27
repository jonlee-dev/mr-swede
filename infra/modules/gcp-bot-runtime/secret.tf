###############################################################################
# Discord bot credentials secret.
#
# Same pattern as gcp-valheim-vm/secret.tf: Terraform owns the secret
# CONTAINER and the IAM binding. The VALUE is seeded out-of-band so the
# Discord token never enters Terraform state.
#
# This secret pre-existed Terraform (it was created by hand back when the
# bot was deployed via click-ops). On the first apply we MUST import it,
# otherwise google_secret_manager_secret will try to create a duplicate
# and fail with HTTP 409:
#
#   terraform -chdir=infra/envs/prod import \
#     module.bot_runtime.google_secret_manager_secret.discord_bot_secrets \
#     projects/mr-swede/secrets/discord-bot-secrets
#
# After the import, `terraform plan` may show drift if the existing secret
# uses automatic replication and we declare user_managed below, or vice
# versa. Adjust the replication block to match the imported state, or
# (if you don't care about region pinning) flip below to `automatic {}`.
#
# Payload format:
#   {
#     "mr-swede": {"id": "...", "token": "...", "public_key": "..."},
#     ...
#   }
# See bot/src/config/secrets.py for the lookup logic.
###############################################################################

resource "google_secret_manager_secret" "discord_bot_secrets" {
  project   = var.project_id
  secret_id = "discord-bot-secrets"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = var.labels
}

# Secret-scoped accessor: the bot SA can read THIS secret only, not every
# secret in the project.
resource "google_secret_manager_secret_iam_member" "bot_can_read_discord_secrets" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.discord_bot_secrets.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.bot.email}"
}
