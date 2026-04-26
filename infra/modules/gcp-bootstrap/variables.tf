variable "project_id" {
  description = "GCP project that hosts the state bucket, WIF pool, and CI service account."
  type        = string
}

variable "github_owner" {
  description = "GitHub user/org that owns the repo — used in the WIF attribute condition."
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name — the WIF principal binding is scoped to this repo."
  type        = string
}

variable "state_bucket_name" {
  description = "Name of the GCS bucket that holds Terraform state. Globally unique. Defaults to '<project>-tfstate'."
  type        = string
  default     = null
}

variable "state_bucket_location" {
  description = "Region for the Terraform state bucket. Defaults to US multi-region (cheap + durable)."
  type        = string
  default     = "US"
}

variable "state_bucket_versions_to_keep" {
  description = "How many noncurrent versions of each state object to keep before GCS deletes them."
  type        = number
  default     = 10
}
