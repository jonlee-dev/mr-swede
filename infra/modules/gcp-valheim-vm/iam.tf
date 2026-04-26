###############################################################################
# Service account attached to the Valheim VM.
#
# Principle of least privilege:
#   - logging.logWriter so the cloud-init + systemd output ships to Cloud
#     Logging (otherwise we'd be SSHing in to debug boot failures).
#   - secretmanager.secretAccessor is granted at the SECRET level in
#     secret.tf, not project-wide -- this SA can only read the one
#     valheim-server-password secret.
#   - No compute admin, no storage admin, no token creator. The VM does
#     not need to manage other resources.
###############################################################################

resource "google_service_account" "valheim_vm" {
  project      = var.project_id
  account_id   = "valheim-vm-sa"
  display_name = "Valheim VM"
  description  = "Runtime identity for the Valheim dedicated server VM."
}

resource "google_project_iam_member" "vm_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.valheim_vm.email}"
}

resource "google_project_iam_member" "vm_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.valheim_vm.email}"
}
