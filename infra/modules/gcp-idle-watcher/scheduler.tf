###############################################################################
# Cloud Scheduler job that fires the function on a cron.
#
# OIDC token: Cloud Scheduler calls the function via HTTP with a
# bearer token signed for the function's URI. The watcher SA must
# have run.invoker on the function (granted in function.tf).
#
# attempt_deadline: how long the scheduler waits for the function to
# return before considering the call a failure. Set generously --
# the function should normally respond in <10s.
#
# retry_count = 0: don't retry on failure. The next scheduled tick
# (in 30 min) will retry naturally; aggressive retries on a transient
# blip would just hammer the compute API for nothing.
###############################################################################

resource "google_cloud_scheduler_job" "watcher" {
  project          = var.project_id
  region           = var.region
  name             = "valheim-idle-watcher-tick"
  description      = "Periodic tick that fires the multi-target idle-watcher function (every ${var.polling_schedule})."
  schedule         = var.polling_schedule
  time_zone        = "Etc/UTC"
  attempt_deadline = "${var.function_timeout_seconds}s"

  # When paused=true, the job stops firing ticks. The function and all
  # its plumbing stay deployed -- only the cron is silenced. Toggle
  # via the `paused` module variable. See the variable's description
  # in variables.tf for the cost trade-off.
  paused = var.paused

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.watcher.service_config[0].uri

    oidc_token {
      service_account_email = google_service_account.watcher.email
      audience              = google_cloudfunctions2_function.watcher.service_config[0].uri
    }
  }

  depends_on = [
    google_cloud_run_v2_service_iam_member.scheduler_can_invoke_watcher,
  ]
}
