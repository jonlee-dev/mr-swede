# Root orchestration for the prod environment.
#
# Phase 0.5 — bootstrap: state bucket, APIs, Workload Identity Federation.
# Phase 1   — Valheim VM: VPC, firewall, persistent disk, server password.
# Later phases (2, 3, 7) will add backups, bot runtime, and the idle watcher.

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
