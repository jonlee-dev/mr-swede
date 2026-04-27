output "service_account_email" {
  description = "Watcher runtime + caller identity. Grant additional access here, never project-wide."
  value       = google_service_account.watcher.email
}

output "function_name" {
  description = "Cloud Function name. Use with `gcloud functions logs read` for debugging."
  value       = google_cloudfunctions2_function.watcher.name
}

output "function_uri" {
  description = "Cloud Run URL backing the 2nd-gen function. Cloud Scheduler POSTs here on each tick."
  value       = google_cloudfunctions2_function.watcher.service_config[0].uri
}

output "scheduler_job_name" {
  description = "Cloud Scheduler job name. Use `gcloud scheduler jobs run` to fire the function manually."
  value       = google_cloud_scheduler_job.watcher.name
}

output "state_bucket_name" {
  description = "GCS bucket holding the empty-check counter state object."
  value       = google_storage_bucket.state.name
}

output "function_source_bucket_name" {
  description = "GCS bucket holding the function source zip. Old zips age out after 30 days."
  value       = google_storage_bucket.function_source.name
}
