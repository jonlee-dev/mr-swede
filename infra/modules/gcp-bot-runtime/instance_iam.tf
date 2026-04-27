###############################################################################
# Compute IAM: instance-scoped, not project-wide.
#
# The bot needs to start/stop exactly one VM. We grant a CUSTOM role
# rather than the predefined roles/compute.instanceAdmin.v1 (which has
# ~50 permissions, of which we use four). The custom role is project-
# scoped but bound to the bot SA at the instance level -- so the
# permissions only apply against the Valheim VM.
#
# Both this module (the bot SA) and the gcp-idle-watcher module (the
# watcher SA) bind to the same role. That's why the role lives here
# but is also surfaced in outputs.tf -- the idle watcher consumes its
# resource name as a module input.
#
# google_compute_instance_iam_member splits the self_link to find the
# instance + zone implicitly, but the resource block needs them spelled
# out. We parse the self_link with regex; cleaner than threading two
# more variables through.
###############################################################################

locals {
  # Self-link shape: projects/<project>/zones/<zone>/instances/<name>
  vm_zone = element(split("/", var.valheim_instance_self_link), 8)
  vm_name = element(split("/", var.valheim_instance_self_link), 10)
}

resource "google_project_iam_custom_role" "vm_controller" {
  project     = var.project_id
  role_id     = "mrSwedeVmController"
  title       = "Mr. Swede VM Controller"
  description = "Minimum permissions to start, stop, and describe the Valheim VM. Used by the bot service and the idle watcher."

  permissions = [
    "compute.instances.get",
    "compute.instances.start",
    "compute.instances.stop",
    # Free insurance: the SDK auto-polls operations on some calls,
    # and any future "wait until RUNNING" feature would need this.
    # Not used today.
    "compute.zoneOperations.get",
  ]

  stage = "GA"
}

resource "google_compute_instance_iam_member" "bot_can_admin_valheim_vm" {
  project       = var.project_id
  zone          = local.vm_zone
  instance_name = local.vm_name
  role          = google_project_iam_custom_role.vm_controller.name
  member        = "serviceAccount:${google_service_account.bot.email}"
}
