output "instance_name" {
  description = "GCE instance name."
  value       = google_compute_instance.bot_vm.name
}

output "instance_zone" {
  description = "Zone where the bot VM runs."
  value       = google_compute_instance.bot_vm.zone
}

output "instance_self_link" {
  description = "Full self-link of the bot VM."
  value       = google_compute_instance.bot_vm.self_link
}

output "public_ip" {
  description = "Ephemeral public IP for outbound (and ssh-via-IAP)."
  value       = google_compute_instance.bot_vm.network_interface[0].access_config[0].nat_ip
}
