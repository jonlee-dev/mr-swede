###############################################################################
# Firewall rule for inbound Lavalink traffic.
#
# Lavalink's REST + WebSocket bind on TCP 2333. The bot connects via
# Cloud Run egress (which uses Google's NAT pool, no static IP
# practically), so the source range is necessarily 0.0.0.0/0. The
# REST/WS layer requires a Bearer-style password header on every
# request, so the open port is auth-protected, not silently public.
#
# IAP SSH access is inherited from the rule on the shared VPC
# (valheim-allow-iap-ssh in gcp-valheim-vm). Lavalink VM picks up
# IAP SSH because both VMs are tagged inside the same VPC's rule set.
# We add a new tag-scoped rule for the audio port so the existing
# tag (valheim-server) doesn't accidentally apply.
###############################################################################

resource "google_compute_firewall" "lavalink_ingress" {
  project     = var.project_id
  name        = "lavalink-allow-rest"
  network     = var.vpc_self_link
  description = "Lavalink REST + WebSocket port (auth-protected by Lavalink itself)."
  direction   = "INGRESS"
  priority    = 1000

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["lavalink-server"]

  allow {
    protocol = "tcp"
    ports    = [tostring(var.lavalink_port)]
  }
}

# IAP SSH for the Lavalink VM. The Valheim module's rule targets
# tag=valheim-server, so we need a parallel rule for the lavalink-server
# tag. Same source range, same TCP 22.
resource "google_compute_firewall" "lavalink_iap_ssh" {
  project     = var.project_id
  name        = "lavalink-allow-iap-ssh"
  network     = var.vpc_self_link
  description = "Allow SSH from Google's IAP tunnel range to the Lavalink VM."
  direction   = "INGRESS"
  priority    = 1000

  source_ranges = [var.iap_ssh_source_range]
  target_tags   = ["lavalink-server"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
