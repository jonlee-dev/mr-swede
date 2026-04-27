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
  description = "Cloud Run min instances. Required = 1 because Discord drops gateway sessions that go idle, so we must keep one instance warm 24/7."
  type        = number
  default     = 1
}

variable "max_instances" {
  description = "Cloud Run max instances. Required = 1 because the bot maintains a single Discord gateway WebSocket -- multiple instances would each open their own session and double-process every event."
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

variable "labels" {
  description = "Labels applied to all resources for cost attribution."
  type        = map(string)
  default = {
    app        = "mr-swede"
    managed_by = "terraform"
  }
}
