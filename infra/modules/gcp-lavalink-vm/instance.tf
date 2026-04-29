###############################################################################
# Startup-script render.
#
# Mirrors the Valheim VM's pattern: templatefile() reads
# server/lavalink/startup-script.sh.tftpl and substitutes $${...}
# placeholders. The five embedded files (compose, application.yml,
# fetch-secrets script, two systemd units) are read with file() and
# inlined as base64 inside heredocs.
#
# We use startup-script (NOT user-data / cloud-init) because GCP's
# standard Debian image (debian-cloud/debian-12) does NOT include
# cloud-init. metadata.startup-script is the canonical mechanism
# Debian-on-GCE honors via google-guest-agent.
###############################################################################

locals {
  server_dir = "${path.module}/../../../server/lavalink"

  startup_script = templatefile("${local.server_dir}/startup-script.sh.tftpl", {
    application_yml                = file("${local.server_dir}/application.yml")
    fetch_secrets_sh               = file("${local.server_dir}/scripts/fetch-secrets.sh")
    lavalink_service               = file("${local.server_dir}/scripts/lavalink.service")
    lavalink_fetch_secrets_service = file("${local.server_dir}/scripts/lavalink-fetch-secrets.service")
  })
}

###############################################################################
# The VM.
###############################################################################

resource "google_compute_instance" "lavalink" {
  project      = var.project_id
  name         = "lavalink-server"
  machine_type = var.machine_type
  zone         = var.zone

  tags = ["lavalink-server"]

  labels = var.labels

  deletion_protection = var.deletion_protection

  boot_disk {
    initialize_params {
      image  = var.boot_disk_image
      size   = var.boot_disk_size_gb
      type   = "pd-balanced"
      labels = var.labels
    }
  }

  network_interface {
    network    = var.vpc_self_link
    subnetwork = var.subnet_self_link

    # Ephemeral public IP. Lavalink streams audio frames to Discord
    # directly, so the VM does need outbound network access; the
    # public IP is the simplest path. The IP can change on every
    # stop/start without breaking anything because the bot looks up
    # the current IP via the compute API at every connect.
    access_config {}
  }

  service_account {
    email  = google_service_account.lavalink_vm.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = {
    # google-guest-agent reads this metadata key on every boot and
    # executes the value as root. Idempotent on re-runs (the script
    # itself is designed to be safe to re-execute).
    startup-script = local.startup_script

    # Same SSH posture as the Valheim VM: no OS Login, no project-wide
    # SSH keys, IAP-tunneled access only.
    enable-oslogin         = "FALSE"
    block-project-ssh-keys = "TRUE"
  }

  allow_stopping_for_update = true

  lifecycle {
    # gcloud compute ssh adds the user's public key to instance
    # metadata every time you SSH in. Without this ignore, every
    # `terraform plan` after an SSH would show drift wanting to
    # remove the key. Specific-key ignore keeps TF in charge of
    # `startup-script` while letting gcloud manage SSH access keys
    # as it always has.
    ignore_changes = [
      metadata["ssh-keys"],
    ]
  }
}
