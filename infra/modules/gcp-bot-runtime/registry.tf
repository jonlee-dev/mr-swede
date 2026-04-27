###############################################################################
# Artifact Registry repo for the bot image.
#
# Cloud Build pushes images here at $_AR_HOSTNAME/$_AR_PROJECT_ID/$_AR_REPOSITORY/...
# (see cloudbuild.yaml at the repo root). The repository ID, hostname, and
# region must all line up with the cloudbuild.yaml substitutions.
#
# The bot lived in us-east4 prior to v3.0.x. This greenfield repo is in
# var.region (default us-central1) -- after the new service is healthy
# the us-east4 AR repo can be deleted by hand. Not in TF because it
# never lived in TF.
###############################################################################

resource "google_artifact_registry_repository" "bot" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_repository_id
  description   = "Container images for the Mr. Swede Cloud Run service."
  format        = "DOCKER"

  labels = var.labels
}
