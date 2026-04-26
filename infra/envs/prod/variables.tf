variable "project_id" {
  description = "GCP project ID hosting all resources (bot, VM, backups)."
  type        = string
}

variable "region" {
  description = "Default GCP region for regional resources."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Default GCP zone for zonal resources (the Valheim VM lives here)."
  type        = string
  default     = "us-central1-a"
}

variable "github_owner" {
  description = "GitHub account/org that owns the repo (for Workload Identity Federation)."
  type        = string
}

variable "github_repo" {
  description = "GitHub repo name (for Workload Identity Federation)."
  type        = string
  default     = "mr-swede"
}

variable "valheim_server_name" {
  description = "Display name shown in the Valheim server browser. Visible to anyone with the join code."
  type        = string
  default     = "mr-swede-valheim"
}

variable "valheim_initial_world" {
  description = "Initial world filename (no extension). The bot rewrites /etc/valheim/world.env to switch worlds without recreating the VM."
  type        = string
  default     = "default"
}
