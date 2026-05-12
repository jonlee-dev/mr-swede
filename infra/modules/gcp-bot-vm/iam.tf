###############################################################################
# Secret-scoped IAM bindings the bot VM needs that don't already exist.
#
# The mr-swede-sa identity (created in gcp-bot-runtime) already has
# accessor on:
#   - discord-bot-secrets        (granted in gcp-bot-runtime/secret.tf)
#   - valheim-server-password    (granted in gcp-bot-runtime/secret.tf)
#   - lavalink-server-password   (granted in gcp-bot-runtime/secret.tf)
#
# The only additional grant the VM needs is read access to
# spotify-client-credentials -- previously only granted to
# lavalink-vm-sa via gcp-lavalink-vm. The new VM hosts Lavalink under
# mr-swede-sa, so we add the binding here.
#
# We deliberately did NOT recreate the service account. Reusing
# mr-swede-sa means one less identity to manage and the entire IAM
# graph (Cloud Build can act-as, custom mrSwedeVmController role
# bindings on Valheim) is inherited for free.
###############################################################################

resource "google_secret_manager_secret_iam_member" "bot_vm_can_read_spotify_credentials" {
  project   = var.project_id
  secret_id = var.spotify_credentials_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.service_account_email}"
}
