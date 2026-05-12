###############################################################################
# Firewall rules for the bot+Lavalink VM.
#
# Inbound posture: DENY ALL except IAP SSH. Big change from the old
# Lavalink VM, which exposed port 2333 to the public internet so the
# Cloud Run bot could reach it. With bot + Lavalink co-tenanted, the
# bot connects via http://localhost:2333 -- no public ingress needed,
# no auth-over-internet attack surface, no DDoS risk on 2333.
#
# Outbound: GCE default-allow-all egress is fine. The bot needs:
#   - Discord gateway WSS + voice UDP (Cloudflare frontend)
#   - GCP APIs (Compute, Secret Manager, Logging)
#   - Valheim VM's status HTTP daemon (port 9001 on its public IP)
#   - Thunderstore (during Lavalink boot for plugin downloads)
###############################################################################

resource "google_compute_firewall" "bot_vm_iap_ssh" {
  project = var.project_id
  name    = "bot-vm-allow-iap-ssh"
  network = var.vpc_self_link

  description = "Allow SSH from Google's IAP source range only. No public 0.0.0.0/0:22."

  source_ranges = [var.iap_ssh_source_range]
  target_tags   = ["bot-vm"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
