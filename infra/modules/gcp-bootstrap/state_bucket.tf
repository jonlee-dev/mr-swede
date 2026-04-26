# -----------------------------------------------------------------------------
# Terraform state bucket.
#
# Versioning is on so that if somebody (or something) destroys or corrupts the
# state file, we can roll back. `uniform_bucket_level_access` disables the older
# ACL-based permission system in favor of IAM — simpler mental model, fewer
# footguns. The lifecycle rule caps retained versions so we don't pay to keep
# thousands of old state snapshots forever.
#
# The bucket is created with `force_destroy = false`: destroying this bucket
# via Terraform will fail if any objects (i.e. state files) remain. That's
# intentional — losing the state bucket on an accidental `terraform destroy`
# would be catastrophic.
# -----------------------------------------------------------------------------
resource "google_storage_bucket" "tf_state" {
  name                        = local.state_bucket_name
  project                     = var.project_id
  location                    = var.state_bucket_location
  force_destroy               = false
  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = var.state_bucket_versions_to_keep
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.services]
}
