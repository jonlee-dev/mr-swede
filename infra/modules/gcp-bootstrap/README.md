# gcp-bootstrap

One-time setup for everything else in `infra/` to work:

- Enables all GCP APIs used across the project.
- Creates the GCS bucket that holds Terraform state.
- Sets up Workload Identity Federation so GitHub Actions can authenticate as a service account *without* any static JSON key in GitHub Secrets.
- Creates a `terraform-ci` service account with scoped roles — this is what Actions impersonates.

This module has a **chicken-and-egg problem**: the state bucket it creates is where we want to store the state for this module itself. The only way around that is to apply the module once using a local backend, then migrate state into the bucket it just created. See `docs/bootstrap.md` for the step-by-step.

## Inputs

| Name | Required | Default | Purpose |
|---|---|---|---|
| `project_id` | yes | — | GCP project hosting everything |
| `github_owner` | yes | — | GitHub user/org (used in WIF attribute condition) |
| `github_repo` | yes | — | GitHub repo (restricts which repo can impersonate the CI SA) |
| `state_bucket_name` | no | `<project>-tfstate` | Name of the TF state bucket |
| `state_bucket_location` | no | `US` | Bucket region/multi-region |
| `state_bucket_versions_to_keep` | no | `10` | Lifecycle cap on noncurrent state versions |

## Outputs

| Name | Use |
|---|---|
| `state_bucket_name` | Paste into `backend.tf` when migrating state |
| `workload_identity_provider` | Pass to `google-github-actions/auth@v2` in CI |
| `terraform_ci_service_account_email` | Same — the SA that Actions impersonates |
| `project_number` | Useful for constructing other resource paths |
