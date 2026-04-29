variable "project_id" {
  description = "GCP project ID hosting the Lavalink VM."
  type        = string
}

variable "region" {
  description = "GCP region for the password secret's regional replica."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone for the VM. Should match the bot's region; cross-region adds latency to every track-resolve call."
  type        = string
  default     = "us-central1-a"
}

variable "machine_type" {
  description = "VM size. Default e2-small (2 vCPU shared, 2GB) handles 1-3 concurrent voice channels comfortably. Bump to e2-medium (1-2 vCPU dedicated, 4GB) if audio glitches under load."
  type        = string
  default     = "e2-small"
}

variable "boot_disk_image" {
  description = "Boot image. Same Debian 12 family as the Valheim VM -- google-guest-agent provides the startup-script handler we depend on."
  type        = string
  default     = "debian-cloud/debian-12"
}

variable "boot_disk_size_gb" {
  description = "Ephemeral boot disk. Lavalink + Docker + plugin jars fit in <5GB; 10GB has plenty of headroom for logs."
  type        = number
  default     = 10
}

variable "vpc_self_link" {
  description = "Self-link of the VPC the Lavalink VM joins. Surfaced from module.valheim_vm so we share network plumbing rather than duplicating it."
  type        = string
}

variable "subnet_self_link" {
  description = "Self-link of the subnet the Lavalink VM joins. Same VPC as Valheim by default."
  type        = string
}

variable "iap_ssh_source_range" {
  description = "Google's published IAP source CIDR. Hard-coded by GCP; override only if Google changes it."
  type        = string
  default     = "35.235.240.0/20"
}

variable "lavalink_port" {
  description = "TCP port the Lavalink REST/WS server binds. Must match server/lavalink/application.yml."
  type        = number
  default     = 2333
}

variable "deletion_protection" {
  description = "Whether to enable deletion_protection on the VM. Off is sometimes useful in test envs; default ON for prod."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Labels applied to all resources for cost attribution."
  type        = map(string)
  default = {
    app        = "mr-swede"
    component  = "lavalink"
    managed_by = "terraform"
  }
}
