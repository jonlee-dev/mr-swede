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

variable "discord_guild_id" {
  description = "Discord guild ID for instant slash-command sync. Empty (default) = global sync (~1hr propagation). Override locally for fast iteration."
  type        = string
  default     = ""
}

variable "music_command_channel_id" {
  description = "Discord channel ID where /music * commands are accepted. Empty = anywhere. Set to your #bot-spam channel ID to scope command invocation. The bot still joins whatever VOICE channel the user is in -- this only governs where the slash command is accepted."
  type        = string
  default     = ""
}

variable "idle_watcher_paused" {
  description = "Pause the idle-watcher Cloud Scheduler job. The function stays deployed; only the cron stops firing. Set true as an emergency off-switch when the watcher's regressing into false-stopping live sessions; flip back to false once the probe is verified. While paused, on-demand VMs (Valheim, Lavalink) will stay up indefinitely until manually stopped via /valheim stop / /music stop / gcloud."
  type        = bool
  default     = false
}
