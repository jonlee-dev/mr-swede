output "instance_name" {
  description = "GCE instance name. The bot uses this to call instances.start / instances.stop."
  value       = google_compute_instance.valheim.name
}

output "instance_zone" {
  description = "Zone of the VM. Required alongside instance_name on every Compute API call."
  value       = google_compute_instance.valheim.zone
}

output "instance_self_link" {
  description = "Full self-link of the VM. Useful for IAM bindings and Cloud Logging filters."
  value       = google_compute_instance.valheim.self_link
}

output "vm_service_account_email" {
  description = "Service account attached to the VM. Grant this email any extra access the VM may need later."
  value       = google_service_account.valheim_vm.email
}

output "world_data_disk_name" {
  description = "Name of the persistent data disk holding world saves. Detach + reattach for recovery."
  value       = google_compute_disk.world_data.name
}

output "server_password_secret_id" {
  description = "Secret Manager secret ID that holds the server password. Seed/rotate with `gcloud secrets versions add`."
  value       = google_secret_manager_secret.server_password.secret_id
}

output "vpc_name" {
  description = "Custom VPC name. Other modules (e.g. backups, peering) should reference this."
  value       = google_compute_network.valheim.name
}

output "subnet_name" {
  description = "Subnet name inside the Valheim VPC."
  value       = google_compute_subnetwork.valheim.name
}

output "vpc_self_link" {
  description = "Full self-link of the custom VPC. Sibling VM modules (e.g. gcp-lavalink-vm) consume this so they share network plumbing rather than creating their own VPC."
  value       = google_compute_network.valheim.self_link
}

output "subnet_self_link" {
  description = "Full self-link of the subnet. Sibling VM modules use this when binding their own google_compute_instance to the same subnet."
  value       = google_compute_subnetwork.valheim.self_link
}
