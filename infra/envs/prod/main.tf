# Root orchestration for the prod environment.
#
#   bootstrap   — state bucket, APIs, Workload Identity Federation.
#   valheim_vm  — VPC, firewall, persistent disk, server password.
#   bot_runtime — Cloud Run service, Cloud Build trigger, AR repo, IAM.
#
# Backups module and the idle watcher will land in their own modules later.

module "bootstrap" {
  source = "../../modules/gcp-bootstrap"

  project_id   = var.project_id
  github_owner = var.github_owner
  github_repo  = var.github_repo
}

module "valheim_vm" {
  source = "../../modules/gcp-valheim-vm"

  project_id  = var.project_id
  region      = var.region
  zone        = var.zone
  server_name = var.valheim_server_name
  world_name  = var.valheim_initial_world

  # The VM module reads project APIs (compute, secretmanager, iap) that
  # bootstrap turns on. Make the dependency explicit so the first apply
  # doesn't race the API enablement.
  depends_on = [module.bootstrap]
}

module "bot_runtime" {
  source = "../../modules/gcp-bot-runtime"

  project_id                 = var.project_id
  region                     = var.region
  valheim_instance_self_link = module.valheim_vm.instance_self_link
  github_owner               = var.github_owner
  github_repo                = var.github_repo
  discord_guild_id           = var.discord_guild_id

  # Same dependency reasoning as valheim_vm: bootstrap enables the run,
  # cloudbuild, and artifactregistry APIs that this module immediately
  # consumes.
  depends_on = [module.bootstrap]
}

module "idle_watcher" {
  source = "../../modules/gcp-idle-watcher"

  project_id                 = var.project_id
  region                     = var.region
  valheim_instance_self_link = module.valheim_vm.instance_self_link
  vm_controller_role_id      = module.bot_runtime.vm_controller_role_id

  # bootstrap enables cloudfunctions/cloudscheduler/eventarc/pubsub APIs
  # that this module consumes; bot_runtime owns the custom role we bind to.
  depends_on = [module.bootstrap, module.bot_runtime]
}

###############################################################################
# Surface bootstrap outputs at the env level so humans running
# `terraform output` can grab them without reaching into the module.
###############################################################################

output "state_bucket_name" {
  value       = module.bootstrap.state_bucket_name
  description = "Paste this into backend.tf to migrate state to GCS."
}

output "workload_identity_provider" {
  value       = module.bootstrap.workload_identity_provider
  description = "Pass to google-github-actions/auth@v2 in .github/workflows/terraform.yml."
}

output "terraform_ci_service_account_email" {
  value       = module.bootstrap.terraform_ci_service_account_email
  description = "SA that GitHub Actions impersonates."
}

###############################################################################
# Surface Valheim VM outputs the bot will need (instance id, secret id, etc.).
###############################################################################

output "valheim_instance_name" {
  value       = module.valheim_vm.instance_name
  description = "GCE instance name. The bot calls instances.start / instances.stop with this."
}

output "valheim_instance_zone" {
  value       = module.valheim_vm.instance_zone
  description = "Zone for the Valheim VM. Required on every Compute API call."
}

output "valheim_password_secret_id" {
  value       = module.valheim_vm.server_password_secret_id
  description = "Secret Manager secret ID. Seed once via `gcloud secrets versions add`."
}

output "valheim_vm_service_account_email" {
  value       = module.valheim_vm.vm_service_account_email
  description = "Runtime identity of the Valheim VM. Grant additional access here, never project-wide."
}

###############################################################################
# Surface bot runtime outputs (Cloud Run URL, secret path, AR repo).
###############################################################################

output "bot_service_url" {
  value       = module.bot_runtime.service_url
  description = "Cloud Run URL of the bot. `curl <url>/health` to smoke-test."
}

output "bot_service_account_email" {
  value       = module.bot_runtime.service_account_email
  description = "Runtime identity of the bot service. Grant any additional access here, never project-wide."
}

output "bot_artifact_registry_repository" {
  value       = module.bot_runtime.artifact_registry_repository
  description = "Container path Cloud Build pushes images to. Mirrors the cloudbuild.yaml _AR_HOSTNAME/_AR_PROJECT_ID/_AR_REPOSITORY substitutions."
}

output "bot_discord_secret_path" {
  value       = module.bot_runtime.discord_secret_path
  description = "DISCORD_SECRET_PATH as wired into the Cloud Run service. Keep in sync with bot/env.example."
}

output "bot_cloudbuild_trigger_id" {
  value       = module.bot_runtime.cloudbuild_trigger_id
  description = "ID of the master-branch trigger. Use with `gcloud builds triggers run` for manual rebuilds."
}

output "vm_controller_role_id" {
  value       = module.bot_runtime.vm_controller_role_id
  description = "Custom role granting minimum compute perms on the Valheim VM. Consumed by the idle-watcher module."
}

###############################################################################
# Surface idle-watcher outputs (function URL, scheduler job, state bucket).
###############################################################################

output "idle_watcher_function_name" {
  value       = module.idle_watcher.function_name
  description = "Cloud Function name. `gcloud functions logs read $name --region=us-central1` to inspect runs."
}

output "idle_watcher_scheduler_job_name" {
  value       = module.idle_watcher.scheduler_job_name
  description = "Cloud Scheduler job name. `gcloud scheduler jobs run $name --location=us-central1` fires it manually."
}

output "idle_watcher_state_bucket" {
  value       = module.idle_watcher.state_bucket_name
  description = "GCS bucket holding state.json (the empty-check counter)."
}

output "idle_watcher_service_account_email" {
  value       = module.idle_watcher.service_account_email
  description = "Watcher SA. Same custom compute role as the bot, instance-scoped to the Valheim VM."
}
