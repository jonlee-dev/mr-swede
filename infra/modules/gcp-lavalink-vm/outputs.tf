output "instance_name" {
  description = "GCE instance name. The bot uses this to call instances.start / instances.stop."
  value       = google_compute_instance.lavalink.name
}

output "instance_zone" {
  description = "Zone of the VM. Required alongside instance_name on every Compute API call."
  value       = google_compute_instance.lavalink.zone
}

output "instance_self_link" {
  description = "Full self-link of the VM. Used for instance-scoped IAM bindings (bot SA, idle-watcher SA)."
  value       = google_compute_instance.lavalink.self_link
}

output "vm_service_account_email" {
  description = "Service account attached to the VM. Grant additional access here, never project-wide."
  value       = google_service_account.lavalink_vm.email
}

output "server_password_secret_id" {
  description = "Secret Manager secret ID that holds the Lavalink server password. Seed/rotate with `gcloud secrets versions add`."
  value       = google_secret_manager_secret.server_password.secret_id
}

output "spotify_credentials_secret_id" {
  description = "Secret Manager secret ID for the Spotify Developer App JSON credentials consumed by lavasrc. Seed/rotate with `gcloud secrets versions add`."
  value       = google_secret_manager_secret.spotify_credentials.secret_id
}

output "lavalink_port" {
  description = "TCP port Lavalink binds. Bot reads this to construct its WebSocket URL."
  value       = var.lavalink_port
}
