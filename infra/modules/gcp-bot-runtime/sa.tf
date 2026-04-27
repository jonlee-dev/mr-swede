###############################################################################
# Bot service account.
#
# Principle of least privilege:
#   - logging.logWriter so structlog output reaches Cloud Logging.
#     (Cloud Run's runtime SA gets this implicitly, but we override the
#     runtime SA with a dedicated one -- see service.tf -- so we have to
#     re-grant.)
#   - monitoring.metricWriter for Cloud Run custom metrics. Harmless if
#     unused; cheap insurance for adding /metrics-style observability later
#     without re-applying IAM.
#   - secretmanager.secretAccessor is granted at the SECRET level in
#     secret.tf, NOT project-wide. The bot can read exactly one secret.
#   - compute.instanceAdmin.v1 is granted at the INSTANCE level in
#     instance_iam.tf. The bot can start/stop exactly one VM.
#
# What we deliberately do NOT grant:
#   - roles/datastore.user. Firestore was removed in v3.0.0; the bot is
#     stateless again.
#   - roles/run.invoker. The bot is not invoking other Cloud Run services.
#     Cloud Run's platform reaches /health internally without an IAM check.
###############################################################################

resource "google_service_account" "bot" {
  project      = var.project_id
  account_id   = "mr-swede-sa"
  display_name = "Mr. Swede Discord Bot"
  description  = "Runtime identity for the Mr. Swede Cloud Run service."
}

resource "google_project_iam_member" "bot_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.bot.email}"
}

resource "google_project_iam_member" "bot_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.bot.email}"
}
