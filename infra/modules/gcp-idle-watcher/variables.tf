variable "project_id" {
  description = "GCP project ID hosting the Cloud Function, Scheduler job, and state bucket."
  type        = string
}

variable "region" {
  description = "Region for the Cloud Function, the state bucket, and the Cloud Scheduler job. Should match the Valheim VM's region for low-latency A2S probes."
  type        = string
  default     = "us-central1"
}

variable "valheim_instance_self_link" {
  description = "Full self_link of the Valheim VM (from module.valheim_vm.instance_self_link). Drives instance-scoped IAM and the env vars consumed by the function."
  type        = string
}

variable "lavalink_instance_self_link" {
  description = "Full self_link of the Lavalink VM (from module.lavalink_vm.instance_self_link). Watcher polls /v4/players on this instance and stops it when idle."
  type        = string
}

variable "lavalink_port" {
  description = "TCP port Lavalink binds (from module.lavalink_vm.lavalink_port). Used to construct the /v4/players URL."
  type        = number
  default     = 2333
}

variable "lavalink_password_secret_id" {
  description = "Secret Manager secret_id holding the Lavalink server password. The watcher reads this at cold-start to authenticate /v4/players requests."
  type        = string
}

variable "vm_controller_role_id" {
  description = "Resource name of the custom role granting compute.instances.{get,start,stop} + zoneOperations.get (from module.bot_runtime.vm_controller_role_id). The watcher SA binds to the same role as the bot SA."
  type        = string
}

variable "valheim_status_http_port" {
  description = "TCP port on the Valheim VM where the log-scraping status server listens. Must match server/scripts/status-server.py and the gcp-valheim-vm firewall rule."
  type        = number
  default     = 9001
}

variable "polling_schedule" {
  description = "Cron expression for Cloud Scheduler. Default `*/30 * * * *` polls every 30 minutes; combined with the default empty_checks_to_stop=2 that yields a 60-90min idle window before stop."
  type        = string
  default     = "*/30 * * * *"
}

variable "empty_checks_to_stop" {
  description = "Number of consecutive empty probes required before the watcher issues instances.stop. With the default 30-min schedule and N=4, the effective idle window is 90-120 min. Bumping this from the original 2 was triggered by a 2026-05-02 incident where the Valheim status daemon's log-tail truncation caused two consecutive false-empty readings mid-session, stopping the VM with 3 active players. The daemon bug is fixed (server/scripts/status-server.py now follows logs incrementally), but a higher empty-checks threshold gives us defense-in-depth: any single false-empty event from a future regression survives one extra cycle before triggering a stop."
  type        = number
  default     = 4
}

variable "status_http_timeout_seconds" {
  description = "Per-probe HTTP timeout for /status.json. Fetches that exceed this are treated as failures and DO NOT count as empty (conservative)."
  type        = number
  default     = 5.0
}

variable "function_memory" {
  description = "Cloud Function memory limit. The function itself does light work, but Cloud Functions 2nd-gen reserves ~80MB for the runtime and the google-cloud-* client libraries push us over 256MB at peak. 512M leaves headroom and is still firmly inside the free tier."
  type        = string
  default     = "512M"
}

variable "function_timeout_seconds" {
  description = "Per-invocation timeout. The function should finish in under 10s; 60s is generous."
  type        = number
  default     = 60
}

variable "labels" {
  description = "Labels applied to all resources for cost attribution."
  type        = map(string)
  default = {
    app        = "mr-swede"
    managed_by = "terraform"
    component  = "idle-watcher"
  }
}
