###############################################################################
# Compute IAM: instance-scoped, not project-wide.
#
# The bot needs to start/stop exactly one VM. roles/compute.instanceAdmin.v1
# at the instance level grants instances.start, instances.stop,
# instances.get, and zoneOperations.get -- the four permissions
# bot/src/services/compute.py actually calls -- and nothing else.
#
# Project-level binding would also work but lets the bot start/stop ANY
# VM, including the GitHub Actions runners or anything someone spins up
# later. Instance-scoped is cheap and keeps the blast radius tight.
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

resource "google_compute_instance_iam_member" "bot_can_admin_valheim_vm" {
  project       = var.project_id
  zone          = local.vm_zone
  instance_name = local.vm_name
  role          = "roles/compute.instanceAdmin.v1"
  member        = "serviceAccount:${google_service_account.bot.email}"
}
