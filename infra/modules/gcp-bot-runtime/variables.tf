variable "project_id" {
  description = "GCP project ID hosting the bot's Cloud Run service, Artifact Registry, and the Discord secret."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry, and the regional replica of the Discord secret. Should match the Valheim VM's region for latency on /valheim status."
  type        = string
  default     = "us-central1"
}

variable "valheim_instance_self_link" {
  description = "Full self_link of the Valheim VM, surfaced from module.valheim_vm.instance_self_link. Used to grant the bot SA instance-scoped compute.instanceAdmin.v1 -- not project-wide."
  type        = string
}

variable "valheim_password_secret_id" {
  description = "Secret Manager secret_id for the Valheim server password (from module.valheim_vm.server_password_secret_id). The bot reads it to include in /valheim status."
  type        = string
}

variable "valheim_status_http_port" {
  description = "TCP port on the Valheim VM where the log-scraping status server listens. Bot fetches /status.json from this for /valheim status."
  type        = number
  default     = 9001
}

variable "lavalink_instance_self_link" {
  description = "Full self_link of the Lavalink VM (from module.lavalink_vm.instance_self_link). Used to grant the bot SA instance-scoped vm-controller role -- same role / different instance from the Valheim binding."
  type        = string
}

variable "lavalink_password_secret_id" {
  description = "Secret Manager secret_id for the Lavalink server password (from module.lavalink_vm.server_password_secret_id). The bot reads it to authenticate to Lavalink's REST/WS API."
  type        = string
}

variable "lavalink_port" {
  description = "TCP port the Lavalink server binds (from module.lavalink_vm.lavalink_port). Wired into the Cloud Run service env var for the bot's WebSocket URL."
  type        = number
  default     = 2333
}

variable "music_command_channel_id" {
  description = "Discord channel ID where /music * commands are accepted. Empty = no restriction (commands work anywhere). Set to your #bot-spam channel ID to scope. The bot still joins whatever VOICE channel the user is in -- this only restricts the slash-command invocation channel."
  type        = string
  default     = ""
}

variable "github_owner" {
  description = "GitHub account or org that owns the bot's source repo (used by the Cloud Build trigger)."
  type        = string
}

variable "github_repo" {
  description = "GitHub repo name to attach the Cloud Build trigger to. Default matches this repo."
  type        = string
  default     = "mr-swede"
}

variable "github_branch_regex" {
  description = "Regex of branches that fire the Cloud Build trigger. Default `^master$` is intentional -- we only deploy from master, not from feature branches."
  type        = string
  default     = "^master$"
}

variable "discord_bot_name" {
  description = "Key into the discord-bot-secrets JSON identifying which bot to run. Wired into the Cloud Run service as DISCORD_BOT_NAME."
  type        = string
  default     = "mr-swede"
}

variable "discord_guild_id" {
  description = "Discord guild ID for instant slash-command sync. Empty string = global sync (~1hr propagation). For prod we leave it empty so commands are visible everywhere; set this when iterating during dev."
  type        = string
  default     = ""
}

variable "service_name" {
  description = "Cloud Run service name. Must match the _SERVICE_NAME substitution in cloudbuild.yaml."
  type        = string
  default     = "mr-swede"
}

variable "artifact_repository_id" {
  description = "Artifact Registry repository ID. Must match the _AR_REPOSITORY substitution in cloudbuild.yaml."
  type        = string
  default     = "cloud-run-source-deploy"
}

variable "min_instances" {
  description = "Cloud Run min instances. 2026-05-12: defaults to 0 because the bot now runs on gcp-bot-vm; this Cloud Run service is kept around as a rollback option but doesn't serve traffic. While the bot was on Cloud Run this was 1 (Discord drops gateway sessions that go idle). To roll back to Cloud Run: set min=1 + max=1 here, scale bot-vm bot.service down on the VM (or destroy the bot-vm module entirely)."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Cloud Run max instances. Still 1 -- we never want concurrent gateway sessions if the service is ever scaled back up. Constraint is the same regardless of which host (Cloud Run vs bot-vm) is the live bot."
  type        = number
  default     = 1
}

variable "cpu" {
  description = "Cloud Run CPU allocation. 1 vCPU is fine for slash-command handling; the bot is mostly idle waiting on the gateway."
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Cloud Run memory. 512Mi handles discord.py + httpx with headroom; the bot is no longer doing audio decoding."
  type        = string
  default     = "512Mi"
}

variable "request_timeout_seconds" {
  description = "Cloud Run request timeout. Set to the max (3600s) because the bot's HTTP server is only used for health checks; long timeouts don't cost anything but prevent spurious 504s during cold start."
  type        = number
  default     = 3600
}

variable "allow_public_invocation" {
  description = "Whether to grant `roles/run.invoker` to allUsers, making /, /health, /metrics publicly hittable. Default true so curl-based smoke tests work without an auth token. The endpoints leak only `bot_ready`, guild count, and gateway latency -- nothing identifiable. Flip to false to lock the service down; smoke tests then need `Authorization: Bearer $(gcloud auth print-identity-token)`."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Labels applied to all resources for cost attribution."
  type        = map(string)
  default = {
    app        = "mr-swede"
    managed_by = "terraform"
  }
}
