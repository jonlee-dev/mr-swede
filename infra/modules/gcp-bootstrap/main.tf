locals {
  state_bucket_name = coalesce(var.state_bucket_name, "${var.project_id}-tfstate")
}

# -----------------------------------------------------------------------------
# Enable the GCP APIs we'll need across every phase of this project.
#
# `google_project_service` is idempotent — re-enabling an already-enabled API
# is a no-op. We set `disable_on_destroy = false` so that tearing down a single
# Terraform resource doesn't accidentally disable an API that other (possibly
# unmanaged) resources depend on.
#
# If an API below causes a cost you weren't expecting, the culprit is almost
# always a resource you later create — enabling the API itself is free.
# -----------------------------------------------------------------------------
resource "google_project_service" "services" {
  for_each = toset([
    # Core platform
    "cloudresourcemanager.googleapis.com", # project metadata + IAM plumbing
    "serviceusage.googleapis.com",         # enables enabling APIs (bootstrap loop)
    "iam.googleapis.com",                  # service accounts, custom roles
    "iamcredentials.googleapis.com",       # required to impersonate SAs (WIF uses this)
    "sts.googleapis.com",                  # Security Token Service — OIDC → GCP exchange
    "logging.googleapis.com",
    "monitoring.googleapis.com",

    # Valheim VM + networking
    "compute.googleapis.com",
    "iap.googleapis.com", # SSH via Identity-Aware Proxy tunnel

    # Storage for state, backups, and bot data
    "storage.googleapis.com",
    "secretmanager.googleapis.com",
    "firestore.googleapis.com",

    # Bot runtime
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",

    # Idle watcher (Phase 7)
    "cloudscheduler.googleapis.com",
    "cloudfunctions.googleapis.com",
    "eventarc.googleapis.com",
    "pubsub.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
