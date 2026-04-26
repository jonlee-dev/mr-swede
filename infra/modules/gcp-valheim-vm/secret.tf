###############################################################################
# Server password secret.
#
# Terraform owns the SECRET (the named container) and the IAM binding, but
# NOT the secret VALUE. Adding a google_secret_manager_secret_version with
# the password as input would put the password into Terraform state in
# plaintext -- exactly what we're trying to avoid by using Secret Manager.
#
# After `terraform apply` the user seeds the value once with:
#
#   echo -n 'CHOOSE-A-PASSWORD' | \
#     gcloud secrets versions add valheim-server-password \
#       --project=$PROJECT_ID --data-file=-
#
# Rotation is the same command with a new value; latest version wins on
# the next VM boot via fetch-secrets.sh.
###############################################################################

resource "google_secret_manager_secret" "server_password" {
  project   = var.project_id
  secret_id = "valheim-server-password"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = var.labels
}

# Secret-scoped access: this SA can read THIS secret only.
resource "google_secret_manager_secret_iam_member" "vm_can_read_password" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.server_password.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.valheim_vm.email}"
}
