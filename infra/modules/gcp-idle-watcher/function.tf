###############################################################################
# Cloud Function 2nd gen.
#
# 2nd gen functions are Cloud Run services under the hood, built by
# Cloud Build, sourced from a zip in GCS. The plumbing:
#
#   archive_file -> zip the function/ dir locally
#       -> google_storage_bucket_object  (upload zip to source bucket)
#           -> google_cloudfunctions2_function.build_config.source.storage_source
#               -> Cloud Build builds the image
#                   -> Cloud Run runs it
#
# The source object name embeds the archive's md5 so a code edit
# triggers a re-upload and a redeploy. Without that, TF would think
# the source is unchanged and skip the rebuild.
#
# ingress_settings: ALLOW_INTERNAL_AND_GCLB lets Cloud Scheduler reach
# the function over Google's internal network. INTERNAL_ONLY is too
# strict (Cloud Scheduler doesn't qualify in all regions), but
# ALLOW_ALL would expose the function to the public internet which
# isn't the intent.
###############################################################################

data "archive_file" "function_source" {
  type        = "zip"
  source_dir  = "${path.module}/function"
  output_path = "${path.module}/.terraform-output/function.zip"
}

resource "google_storage_bucket" "function_source" {
  project                     = var.project_id
  name                        = "${var.project_id}-idle-watcher-source"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true

  # Old function zips pile up otherwise -- one per code change. Auto-
  # delete after 30 days; only the most recent is referenced by the
  # function anyway.
  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }

  labels = var.labels
}

resource "google_storage_bucket_object" "function_source" {
  # Embed the zip's md5 so a code edit forces a re-upload + redeploy.
  name   = "function-${data.archive_file.function_source.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.function_source.output_path
}

resource "google_cloudfunctions2_function" "watcher" {
  project     = var.project_id
  name        = "valheim-idle-watcher"
  location    = var.region
  description = "Polls the Valheim VM's status HTTP endpoint; stops the VM after ${var.empty_checks_to_stop} consecutive empty checks."

  build_config {
    runtime     = "python312"
    entry_point = "check_and_stop"

    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.function_source.name
      }
    }
  }

  service_config {
    max_instance_count    = 1
    available_memory      = var.function_memory
    timeout_seconds       = var.function_timeout_seconds
    service_account_email = google_service_account.watcher.email
    ingress_settings      = "ALLOW_INTERNAL_AND_GCLB"

    environment_variables = {
      GCP_PROJECT                         = var.project_id
      VALHEIM_ZONE                        = local.vm_zone
      VALHEIM_INSTANCE_NAME               = local.vm_name
      VALHEIM_STATUS_HTTP_PORT            = var.valheim_status_http_port
      VALHEIM_STATUS_HTTP_TIMEOUT_SECONDS = var.status_http_timeout_seconds
      IDLE_WATCHER_STATE_BUCKET           = google_storage_bucket.state.name
      IDLE_WATCHER_EMPTY_CHECKS_TO_STOP   = var.empty_checks_to_stop
    }
  }

  labels = var.labels

  depends_on = [
    google_compute_instance_iam_member.watcher_can_admin_valheim_vm,
    google_storage_bucket_iam_member.watcher_can_rw_state,
  ]
}

# Cloud Scheduler authenticates as the watcher SA and calls the
# function's underlying Cloud Run URL. Self-invocation: the watcher SA
# is both the function's runtime AND the scheduler's caller. Cleaner
# than maintaining a separate "scheduler" SA.
resource "google_cloud_run_v2_service_iam_member" "scheduler_can_invoke_watcher" {
  project  = var.project_id
  location = google_cloudfunctions2_function.watcher.location
  name     = google_cloudfunctions2_function.watcher.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.watcher.email}"
}
