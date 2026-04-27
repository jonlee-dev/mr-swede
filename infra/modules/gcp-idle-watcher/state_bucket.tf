###############################################################################
# State bucket: holds a single tiny JSON object with the empty-check counter.
#
# Why GCS over Firestore?
#   - We deleted Firestore in v3.0.0 and don't want to re-introduce it
#     for one integer.
#   - GCS object operations are pennies-per-million; reading and
#     writing one JSON object every 30 minutes is effectively free.
#   - GCS is durable enough -- if the object goes missing, the function
#     starts fresh (consecutive_empty = 0), which is the safe default.
#
# uniform_bucket_level_access: forces IAM-only access (no per-object
# ACLs). Standard for new buckets and aligns with our other buckets.
#
# force_destroy: lets `terraform destroy` clean up the bucket even
# when the state object is present. The data is ephemeral (gets
# rewritten every poll), so deletion is safe.
###############################################################################

resource "google_storage_bucket" "state" {
  project                     = var.project_id
  name                        = "${var.project_id}-idle-watcher-state"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true

  labels = var.labels
}

resource "google_storage_bucket_iam_member" "watcher_can_rw_state" {
  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.watcher.email}"
}
