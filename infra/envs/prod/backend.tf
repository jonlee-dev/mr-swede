# The state backend.
#
# Two-stage bootstrap:
#
#   Stage 1 — local backend (current state of this file).
#     The FIRST `terraform apply` runs with state on your laptop. This creates
#     the GCS state bucket (via module.bootstrap) along with the WIF plumbing.
#
#   Stage 2 — migrate to GCS.
#     After the first apply succeeds:
#       (a) Read the bucket name: `terraform output state_bucket_name`
#       (b) Swap the backend blocks below — uncomment `backend "gcs"`, fill in
#           the bucket name, and comment out `backend "local"`.
#       (c) Run: terraform init -migrate-state
#           When prompted, confirm copying state into GCS.
#
# Full walkthrough: docs/bootstrap.md

terraform {
  backend "local" {
    path = "terraform.tfstate"
  }

  # backend "gcs" {
  #   bucket = "REPLACE-ME-project-id-tfstate"
  #   prefix = "envs/prod"
  # }
}
