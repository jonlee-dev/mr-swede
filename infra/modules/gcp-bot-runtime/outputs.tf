output "service_name" {
  description = "Cloud Run service name. Useful for `gcloud run services logs read`."
  value       = google_cloud_run_v2_service.bot.name
}

output "service_url" {
  description = "Public URL of the Cloud Run service. Hit /health on this to smoke-test deploys."
  value       = google_cloud_run_v2_service.bot.uri
}

output "service_account_email" {
  description = "Bot runtime SA. Surface this so the env-level outputs can show it in `terraform output`."
  value       = google_service_account.bot.email
}

output "artifact_registry_repository" {
  description = "AR repo path in the form <region>-docker.pkg.dev/<project>/<repo>. Cloud Build pushes images under this."
  value       = "${google_artifact_registry_repository.bot.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.bot.repository_id}"
}

output "discord_bot_secrets_secret_id" {
  description = "GSM secret ID holding the Discord token JSON. Seed/rotate with `gcloud secrets versions add discord-bot-secrets`."
  value       = google_secret_manager_secret.discord_bot_secrets.secret_id
}

output "discord_secret_path" {
  description = "Full versioned secret path the bot uses at runtime (DISCORD_SECRET_PATH env var)."
  value       = local.discord_secret_path
}

output "valheim_password_secret_path" {
  description = "Full versioned secret path for the Valheim server password. The bot reads this at runtime for /valheim status (VALHEIM_PASSWORD_SECRET_PATH env var). Surfaced so gcp-bot-vm can pass it through to the bot's env file."
  value       = local.valheim_password_secret_path
}

output "cloudbuild_trigger_id" {
  description = "ID of the Cloud Build trigger watching master. Useful for `gcloud builds triggers run`."
  value       = google_cloudbuild_trigger.bot_master.trigger_id
}

output "vm_controller_role_id" {
  description = "Resource name of the custom role granting minimum perms (instances.{get,start,stop} + zoneOperations.get) for the Valheim VM. Reused by the idle watcher."
  value       = google_project_iam_custom_role.vm_controller.name
}
