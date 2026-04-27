###############################################################################
# Dedicated VPC for the Valheim VM.
#
# Why a custom VPC instead of `default`?
#   - The default VPC is auto-mode (subnet per region) and ships with broad
#     "default-allow-*" firewall rules. Locking down rules on the default VPC
#     also affects every other resource in the project.
#   - A custom VPC gives us one explicit subnet, a clean rule set, and lets
#     us delete the whole network when the project is decommissioned.
###############################################################################

resource "google_compute_network" "valheim" {
  project                 = var.project_id
  name                    = "valheim-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
  description             = "VPC for the Valheim dedicated server."
}

resource "google_compute_subnetwork" "valheim" {
  project                  = var.project_id
  name                     = "valheim-subnet"
  region                   = var.region
  network                  = google_compute_network.valheim.id
  ip_cidr_range            = var.vpc_cidr
  private_ip_google_access = true
  description              = "Single subnet hosting the Valheim VM."
}

###############################################################################
# Firewall rules.
#
# Rule 1: SSH via IAP only.
#   IAP tunnels SSH connections from Google's edge to the VM through a
#   token-authenticated proxy -- no public port 22 ever. Only the published
#   35.235.240.0/20 range can reach the instance, and even then the user
#   needs IAM (roles/iap.tunnelResourceAccessor) to open the tunnel.
#
# Rule 2: Valheim game traffic.
#   Crossplay servers need 2456-2458/UDP open to the world; PlayFab join
#   codes don't bypass the firewall, the IP is just hidden from the player.
#   No TCP rule -- the dedicated server uses UDP only.
###############################################################################

resource "google_compute_firewall" "iap_ssh" {
  project     = var.project_id
  name        = "valheim-allow-iap-ssh"
  network     = google_compute_network.valheim.name
  description = "Allow SSH from Google's IAP tunnel range only."
  direction   = "INGRESS"
  priority    = 1000

  source_ranges = [var.iap_ssh_source_range]
  target_tags   = ["valheim-server"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_firewall" "valheim_udp" {
  project     = var.project_id
  name        = "valheim-allow-game-udp"
  network     = google_compute_network.valheim.name
  description = "Valheim dedicated server ports (UDP) for the public internet."
  direction   = "INGRESS"
  priority    = 1000

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["valheim-server"]

  allow {
    protocol = "udp"
    ports    = ["2456-2458"]
  }
}

# STATUS_HTTP: lloesche image exposes /status.json on a TCP port with
# the PlayFab join code and live player count. The bot's /valheim
# status reads it; we'd also rather not put a load balancer in front
# of one VM, so the port is open to the public internet directly.
# Nothing in /status.json is secret (everything in it is also visible
# via Steam A2S on UDP 2457, which is already public).
resource "google_compute_firewall" "valheim_status_http" {
  project     = var.project_id
  name        = "valheim-allow-status-http"
  network     = google_compute_network.valheim.name
  description = "Valheim STATUS_HTTP (TCP 9001). Returns JSON with join code + player count; nothing secret."
  direction   = "INGRESS"
  priority    = 1000

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["valheim-server"]

  allow {
    protocol = "tcp"
    ports    = [tostring(var.status_http_port)]
  }
}
