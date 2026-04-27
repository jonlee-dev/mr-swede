variable "project_id" {
  description = "GCP project ID hosting the Valheim VM."
  type        = string
}

variable "region" {
  description = "GCP region for the VPC subnet and (regional replica of) the password secret."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone for the VM and persistent data disk. Must be inside var.region."
  type        = string
  default     = "us-central1-a"
}

variable "machine_type" {
  description = "VM size. Default e2-standard-2 (2 vCPU, 8GB) is the right shape for 4-6 concurrent Valheim players."
  type        = string
  default     = "e2-standard-2"
}

variable "boot_disk_image" {
  description = "Boot image. Debian 12 keeps things small and gets us a recent enough kernel for Docker without surprises."
  type        = string
  default     = "debian-cloud/debian-12"
}

variable "boot_disk_size_gb" {
  description = "Ephemeral boot disk size. 10GB fits Debian + Docker images comfortably; world data lives on the data disk."
  type        = number
  default     = 10
}

variable "data_disk_size_gb" {
  description = "Persistent world-data disk size. 20GB is generous for ~3 worlds + backups; pd-balanced gives good IOPS for the cost."
  type        = number
  default     = 20
}

variable "data_disk_type" {
  description = "Persistent data disk type. pd-balanced is the cost/IOPS sweet spot for Valheim."
  type        = string
  default     = "pd-balanced"
}

variable "server_name" {
  description = "Display name shown in the Valheim server browser. Visible to anyone with the join code."
  type        = string
  default     = "mr-swede-valheim"
}

variable "world_name" {
  description = "Initial world filename (no extension). The bot rewrites /etc/valheim/world.env to switch worlds without recreating the VM."
  type        = string
  default     = "default"
}

variable "vpc_cidr" {
  description = "Subnet CIDR for the dedicated Valheim VPC. /24 is more than enough for a single VM."
  type        = string
  default     = "10.10.0.0/24"
}

variable "iap_ssh_source_range" {
  description = "Google's published IAP source CIDR. Hard-coded by GCP; override only if Google changes it."
  type        = string
  default     = "35.235.240.0/20"
}

variable "deletion_protection" {
  description = "Whether to enable deletion_protection on the VM and data disk. Off is sometimes useful in test envs; default ON for prod."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Labels applied to all resources for cost attribution."
  type        = map(string)
  default = {
    app        = "valheim"
    managed_by = "terraform"
  }
}
