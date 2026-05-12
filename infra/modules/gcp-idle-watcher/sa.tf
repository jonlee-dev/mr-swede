###############################################################################
# Watcher service account.
#
# Three responsibilities, one SA:
#
#   1. Run as the Cloud Function's runtime identity (service_account_email
#      on the function's service_config).
#   2. Authenticate to compute.googleapis.com to start/stop/describe
#      the Valheim VM, via the same custom role as the bot.
#   3. Read/write the tiny state object in GCS (object-level IAM,
#      scoped to the state bucket, NOT project-wide).
#
# Plus it's the OIDC identity Cloud Scheduler uses to call the function
# (see scheduler.tf), so we also grant it run.invoker on its own
# function. Self-invocation is unusual but works -- the watcher SA is
# both the function's runtime identity and the scheduler's caller.
###############################################################################

resource "google_service_account" "watcher" {
  project      = var.project_id
  account_id   = "idle-watcher-sa"
  display_name = "Mr. Swede Idle Watcher"
  description  = "Runtime + caller identity for the multi-target idle-watcher Cloud Function (Valheim + Lavalink)."
}

resource "google_project_iam_member" "watcher_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.watcher.email}"
}

# The bot module exports its self_link parsing as locals; we redo it
# here so we don't introduce a cross-module dependency just for string
# operations on the same self_link format.
locals {
  vm_zone = element(split("/", var.valheim_instance_self_link), 8)
  vm_name = element(split("/", var.valheim_instance_self_link), 10)

  # Lavalink target is OPTIONAL as of 2026-05-10. When
  # lavalink_instance_self_link is "" we skip the zone/name parsing
  # entirely; the watcher's Python ALSO sees empty
  # LAVALINK_INSTANCE_NAME and skips the lavalink TARGETS entry.
  lavalink_target_enabled = var.lavalink_instance_self_link != ""
  lavalink_vm_zone        = local.lavalink_target_enabled ? element(split("/", var.lavalink_instance_self_link), 8) : ""
  lavalink_vm_name        = local.lavalink_target_enabled ? element(split("/", var.lavalink_instance_self_link), 10) : ""
}

resource "google_compute_instance_iam_member" "watcher_can_admin_valheim_vm" {
  project       = var.project_id
  zone          = local.vm_zone
  instance_name = local.vm_name
  role          = var.vm_controller_role_id
  member        = "serviceAccount:${google_service_account.watcher.email}"
}

# Same custom role on the Lavalink VM -- only created when the
# Lavalink target is configured. After the 2026-05-10 migration
# where Lavalink became always-on (and the lavalink-server VM was
# retired), this resource is suppressed.
resource "google_compute_instance_iam_member" "watcher_can_admin_lavalink_vm" {
  count = local.lavalink_target_enabled ? 1 : 0

  project       = var.project_id
  zone          = local.lavalink_vm_zone
  instance_name = local.lavalink_vm_name
  role          = var.vm_controller_role_id
  member        = "serviceAccount:${google_service_account.watcher.email}"
}

# Read access on the Lavalink password secret. Same conditional --
# only granted when the Lavalink target is being watched.
resource "google_secret_manager_secret_iam_member" "watcher_can_read_lavalink_password" {
  count = local.lavalink_target_enabled ? 1 : 0

  project   = var.project_id
  secret_id = var.lavalink_password_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.watcher.email}"
}
