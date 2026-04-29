###############################################################################
# Service account attached to the Lavalink VM.
#
# Same shape as the Valheim VM SA -- minimal grants: log + metric
# writers project-wide, and the password-secret reader is granted at
# the SECRET level in secret.tf (not project-wide).
#
# The Lavalink VM does not need any compute permissions on itself --
# it doesn't drive its own lifecycle. The bot SA + idle-watcher SA
# both control this VM via the shared mrSwedeVmController custom role
# (granted at the instance level by gcp-bot-runtime / gcp-idle-watcher
# respectively).
###############################################################################

resource "google_service_account" "lavalink_vm" {
  project      = var.project_id
  account_id   = "lavalink-vm-sa"
  display_name = "Lavalink VM"
  description  = "Runtime identity for the Lavalink audio server VM."
}

resource "google_project_iam_member" "vm_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.lavalink_vm.email}"
}

resource "google_project_iam_member" "vm_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.lavalink_vm.email}"
}
