###############################################################################
# Cloud Build IAM.
#
# Cloud Build runs the cloudbuild.yaml at the repo root using its
# *default* service account. As of GCP's 2024 default change, that's the
# project's compute default SA: <project-number>-compute@developer.gserviceaccount.com
# (NOT the legacy <project-number>@cloudbuild.gserviceaccount.com).
#
# The build SA needs three pieces of access to do its three jobs:
#
#   1. Push images to Artifact Registry      → roles/artifactregistry.writer
#   2. Update the Cloud Run service          → roles/run.admin (project)
#   3. Run the service AS the bot SA         → roles/iam.serviceAccountUser
#                                              on the bot SA itself
#
# `roles/run.admin` at the project level is wider than strictly needed
# (we only update one service), but Cloud Build doesn't have a tidy
# service-scoped option for `gcloud run services update` to work. The
# alternative is a custom role; not worth the maintenance burden for a
# one-bot project.
###############################################################################

locals {
  cloudbuild_sa_email = "${data.google_project.current.number}-compute@developer.gserviceaccount.com"
  cloudbuild_sa       = "serviceAccount:${local.cloudbuild_sa_email}"
}

# 1. Write images into the AR repo (repo-scoped, not project-wide).
resource "google_artifact_registry_repository_iam_member" "build_can_push_images" {
  project    = var.project_id
  location   = google_artifact_registry_repository.bot.location
  repository = google_artifact_registry_repository.bot.name
  role       = "roles/artifactregistry.writer"
  member     = local.cloudbuild_sa
}

# 2. Update the Cloud Run service. Project-level binding -- see comment above.
resource "google_project_iam_member" "build_can_deploy_run" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = local.cloudbuild_sa
}

# 3. ActAs the bot SA. Required for any deploy that sets the service's
#    runAs to a non-default SA (Cloud Run rejects the deploy otherwise).
resource "google_service_account_iam_member" "build_can_act_as_bot" {
  service_account_id = google_service_account.bot.name
  role               = "roles/iam.serviceAccountUser"
  member             = local.cloudbuild_sa
}
