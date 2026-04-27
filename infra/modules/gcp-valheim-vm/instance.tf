###############################################################################
# Cloud-init render.
#
# templatefile() reads server/cloud-init.yaml.tftpl and substitutes ${...}
# placeholders. The four embedded files (compose, fetch-secrets script,
# two systemd units) are read with file() and inlined as base64 inside
# write_files -- this keeps the artifacts as standalone, lintable files
# in server/ rather than escaped heredocs in this module.
###############################################################################

locals {
  server_dir = "${path.module}/../../../server"

  cloud_init = templatefile("${local.server_dir}/cloud-init.yaml.tftpl", {
    docker_compose                = file("${local.server_dir}/docker-compose.yml")
    fetch_secrets_sh              = file("${local.server_dir}/scripts/fetch-secrets.sh")
    valheim_service               = file("${local.server_dir}/scripts/valheim.service")
    valheim_fetch_secrets_service = file("${local.server_dir}/scripts/valheim-fetch-secrets.service")
    server_name                   = var.server_name
    world_name                    = var.world_name
    # Stable Linux device path. GCE exposes attached disks under
    # /dev/disk/by-id/google-<device_name>; we set device_name below.
    data_disk_device = "/dev/disk/by-id/google-valheim-data"
  })
}

###############################################################################
# The VM.
###############################################################################

resource "google_compute_instance" "valheim" {
  project      = var.project_id
  name         = "valheim-server"
  machine_type = var.machine_type
  zone         = var.zone

  tags = ["valheim-server"]

  labels = var.labels

  # deletion_protection is paired with the disk's prevent_destroy.
  # Together they make "oops, terraform destroy" a two-step deliberate act.
  deletion_protection = var.deletion_protection

  boot_disk {
    initialize_params {
      image  = var.boot_disk_image
      size   = var.boot_disk_size_gb
      type   = "pd-balanced"
      labels = var.labels
    }
  }

  attached_disk {
    source      = google_compute_disk.world_data.id
    device_name = "valheim-data" # surfaces as /dev/disk/by-id/google-valheim-data
    mode        = "READ_WRITE"
  }

  network_interface {
    network    = google_compute_network.valheim.id
    subnetwork = google_compute_subnetwork.valheim.id

    # Ephemeral public IP. We don't use a static IP because PlayFab
    # crossplay join codes hide the IP from the player -- the IP can
    # change on every stop/start without anyone caring.
    access_config {}
  }

  service_account {
    email  = google_service_account.valheim_vm.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = {
    # cloud-init reads from this metadata key by default on Debian images.
    user-data = local.cloud_init

    # OS Login is overkill for a one-VM project; we use IAP-tunneled SSH
    # with project-level IAM instead.
    enable-oslogin = "FALSE"

    # Block project-wide SSH keys -- only key-allowlisted users via IAP.
    block-project-ssh-keys = "TRUE"
  }

  # Allow stop-then-update for fields that require it (e.g. machine_type).
  allow_stopping_for_update = true

  lifecycle {
    # Cloud-init runs ONCE at first boot. Re-rendering doesn't re-bootstrap
    # the VM, so we ignore the whole metadata map -- otherwise every edit
    # to server/* would force VM replacement and lose the world data disk
    # association. The other metadata keys (enable-oslogin,
    # block-project-ssh-keys) are static so ignoring the map is safe.
    ignore_changes = [
      metadata,
    ]
  }
}
