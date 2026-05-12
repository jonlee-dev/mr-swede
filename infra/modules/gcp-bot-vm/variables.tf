###############################################################################
# Inputs for the bot+Lavalink co-tenanted VM.
#
# This module owns the COMPUTE for the bot. The IDENTITY (mr-swede-sa),
# the SECRET CONTAINERS (discord-bot-secrets, valheim/lavalink passwords),
# the CUSTOM ROLE (mrSwedeVmController), and Cloud Build / Artifact
# Registry plumbing all stay in gcp-bot-runtime -- this module just
# provisions a VM, mounts it onto the existing IAM/secret graph, and
# runs the bot + Lavalink as systemd units.
#
# 2026-05-10 architecture decision: we moved the bot off Cloud Run onto
# this VM, bundling it with Lavalink on the same box. See the PRD's
# decisions log for the cost + ops tradeoffs. Lavalink no longer needs
# a public IP because the bot reaches it via localhost:2333.
###############################################################################

variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "GCP region. Should match where the existing Valheim VPC lives."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone. Same-zone as the Valheim VM keeps in-region latency low; the bot calls compute.googleapis.com for /valheim status frequently and same-zone is the cheapest path."
  type        = string
  default     = "us-central1-a"
}

variable "machine_type" {
  description = "VM size. e2-small (2 vCPU shared, 2GB RAM) is the planned default per the 2026-05-10 sizing decision: bot ~500MB + Lavalink JVM at -Xmx512m ~1GB + OS = ~1.8GB peak, fits in 2GB with some swap headroom. Bump to e2-medium (4GB) if we observe OOM or GC pressure."
  type        = string
  default     = "e2-small"
}

variable "boot_disk_image" {
  description = "Boot image. Debian 12 -- same as the other VMs for consistency. Has the google-guest-agent that runs metadata.startup-script."
  type        = string
  default     = "debian-cloud/debian-12"
}

variable "boot_disk_size_gb" {
  description = "Boot disk size. Needs to hold: OS (~2GB) + Python venv with discord.py/wavelink/httpx (~300MB) + JDK 17 (~400MB) + Lavalink.jar + plugins (~250MB) + bot repo (~50MB) + logs/cache. 15GB has comfortable headroom; 10GB would be tight."
  type        = number
  default     = 15
}

variable "vpc_self_link" {
  description = "Shared VPC self-link. Same VPC as the Valheim + (old) Lavalink VMs."
  type        = string
}

variable "subnet_self_link" {
  description = "Shared subnet self-link."
  type        = string
}

variable "iap_ssh_source_range" {
  description = "Google's published IAP source CIDR. Hard-coded by GCP; override only if Google publishes a new range."
  type        = string
  default     = "35.235.240.0/20"
}

variable "service_account_email" {
  description = "Service account to attach to the VM. We pass in mr-swede-sa (from gcp-bot-runtime) rather than creating a fresh SA -- mr-swede-sa already has every IAM grant the bot needs (Discord secrets, Valheim secrets, Lavalink password, custom mrSwedeVmController role on Valheim VM)."
  type        = string
}

variable "discord_secret_path" {
  description = "Fully-qualified GSM resource path for the Discord bot token. Same value the Cloud Run service was using; passed through so the systemd bot unit can set DISCORD_SECRET_PATH."
  type        = string
}

variable "lavalink_password_secret_id" {
  description = "Secret ID (NOT full path) for the Lavalink server password. Used by Lavalink's fetch-secrets script which constructs the full path at boot from the VM's project ID."
  type        = string
}

variable "spotify_credentials_secret_id" {
  description = "Secret ID for the Spotify Developer App credentials. Used by Lavalink's lavasrc plugin."
  type        = string
  default     = "spotify-client-credentials"
}

variable "valheim_password_secret_path" {
  description = "Fully-qualified GSM resource path for the Valheim server password. The bot reads this at runtime for /valheim status."
  type        = string
}

variable "lavalink_port" {
  description = "TCP port Lavalink binds on. Localhost-only after this migration; not exposed to the public internet."
  type        = number
  default     = 2333
}

variable "discord_guild_id" {
  description = "Discord guild (server) ID for instant slash-command syncing."
  type        = string
}

variable "discord_bot_name" {
  description = "Key into the discord-bot-secrets JSON. Same default as the Cloud Run service config."
  type        = string
  default     = "mr-swede"
}

variable "music_command_channel_id" {
  description = "Channel ID where /music * is accepted. Same value as the Cloud Run service."
  type        = string
}

variable "valheim_instance_name" {
  description = "Valheim instance name; consumed by the bot for /valheim commands."
  type        = string
  default     = "valheim-server"
}

variable "valheim_zone" {
  description = "Valheim instance zone."
  type        = string
  default     = "us-central1-a"
}

variable "valheim_status_http_port" {
  description = "Port the Valheim status daemon listens on for /v4/info-style probes from /valheim status."
  type        = number
  default     = 9001
}

variable "bot_git_repo" {
  description = "Git repo to clone for the bot. The startup-script clones this on first boot and `git pull` is the deploy mechanism (manual SSH-based per the 2026-05-10 design decision)."
  type        = string
  default     = "https://github.com/jonlee-dev/mr-swede.git"
}

variable "bot_git_ref" {
  description = "Git ref (branch or tag) to track. master == follows latest pushes; tag if you want a pinned release."
  type        = string
  default     = "master"
}

variable "deletion_protection" {
  description = "Enable deletion_protection on the VM. ON in prod to prevent accidental destroy via TF."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Labels applied to all resources for cost attribution."
  type        = map(string)
  default = {
    app        = "mr-swede"
    component  = "bot-vm"
    managed_by = "terraform"
  }
}
