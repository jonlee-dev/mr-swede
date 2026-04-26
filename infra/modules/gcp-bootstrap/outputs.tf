output "state_bucket_name" {
  description = "GCS bucket holding Terraform state. Put this in backend.tf after the initial apply."
  value       = google_storage_bucket.tf_state.name
}

output "workload_identity_provider" {
  description = "Full resource name of the WIF provider. Pass this to google-github-actions/auth@v2 in CI."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "terraform_ci_service_account_email" {
  description = "Email of the SA that GitHub Actions impersonates."
  value       = google_service_account.terraform_ci.email
}

output "project_number" {
  description = "GCP project number (distinct from project_id) — useful when constructing resource paths."
  value       = data.google_project.this.number
}

data "google_project" "this" {
  project_id = var.project_id
}
