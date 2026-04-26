###############################################################################
# Persistent data disk.
#
# Why a separate disk?
#   - Boot disk lifecycle is tied to the VM. If we destroy/recreate the VM
#     (terraform apply with a forced replacement), we DO NOT want world saves
#     to vanish.
#   - Detaching this disk and reattaching it to a new VM is the single-step
#     recovery path -- documented in docs/runbook.md.
#
# deletion_protection on the disk itself prevents `terraform destroy` from
# wiping world data. To actually delete it you have to flip the flag, apply,
# then destroy -- the deliberate two-step is the point.
#
# The cloud-init step formats this disk on first boot (mkfs.ext4 -F) and
# mounts it at /opt/valheim/data. The "google-valheim-data" device name
# on the instance attached_disk block is what makes /dev/disk/by-id/
# resolution stable.
###############################################################################

resource "google_compute_disk" "world_data" {
  project = var.project_id
  name    = "valheim-world-data"
  type    = var.data_disk_type
  zone    = var.zone
  size    = var.data_disk_size_gb

  labels = var.labels

  lifecycle {
    prevent_destroy = true
  }
}
