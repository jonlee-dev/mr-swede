###############################################################################
# Bot + Lavalink co-tenanted VM.
#
# Mirrors the gcp-valheim-vm / gcp-lavalink-vm pattern: render a
# startup-script template via `templatefile()`, embed runtime
# artifacts (systemd units, fetch-secrets scripts, lavalink config) as
# base64 blobs inside the script, drop them on disk on every boot
# (idempotent), enable + restart the relevant systemd units.
#
# The bot itself lives at /opt/mr-swede (git clone) -- the
# startup-script clones the repo on first boot and creates a Python
# venv. Manual deploys are `ssh into VM; cd /opt/mr-swede; git pull;
# poetry install; sudo systemctl restart bot`. Documented in
# docs/runbook.md.
###############################################################################

locals {
  bot_vm_dir = "${path.module}/../../../server/bot-vm"

  # The Lavalink runtime artifacts live in server/lavalink/ -- same
  # files the old gcp-lavalink-vm used. We re-read them here so we
  # have a single source of truth for Lavalink config across both
  # modules (during the migration window) and after.
  lavalink_dir = "${path.module}/../../../server/lavalink"

  startup_script = templatefile("${local.bot_vm_dir}/startup-script.sh.tftpl", {
    # Bot runtime artifacts.
    bot_service          = file("${local.bot_vm_dir}/scripts/bot.service")
    bot_env_template     = file("${local.bot_vm_dir}/scripts/bot.env.tftpl")
    bot_watchdog_sh      = file("${local.bot_vm_dir}/scripts/bot-watchdog.sh")
    bot_watchdog_service = file("${local.bot_vm_dir}/scripts/bot-watchdog.service")
    bot_watchdog_timer   = file("${local.bot_vm_dir}/scripts/bot-watchdog.timer")
    bot_fetch_secrets_sh = file("${local.bot_vm_dir}/scripts/bot-fetch-secrets.sh")
    bot_fetch_secrets_service = file(
      "${local.bot_vm_dir}/scripts/bot-fetch-secrets.service"
    )

    # Lavalink runtime artifacts -- reused as-is from server/lavalink/
    # (the OLD gcp-lavalink-vm's tree). Same application.yml + same
    # systemd units + same fetch-secrets.sh. The Lavalink-side
    # config doesn't change for co-tenancy; only the firewall
    # closes off port 2333 and the bot is configured to talk to
    # localhost. The application.yml has `server.address: 0.0.0.0`
    # but firewall denies external 2333, so binding on all
    # interfaces is harmless and saves us a config-divergence.
    lavalink_application_yml = file("${local.lavalink_dir}/application.yml")
    lavalink_service         = file("${local.lavalink_dir}/scripts/lavalink.service")
    lavalink_fetch_secrets_sh = file(
      "${local.lavalink_dir}/scripts/fetch-secrets.sh"
    )
    lavalink_fetch_secrets_service = file(
      "${local.lavalink_dir}/scripts/lavalink-fetch-secrets.service"
    )

    # Substituted into bot.env at template-render time so the bot's
    # env-file lands fully resolved on disk -- no separate boot-time
    # interpolation step. Secrets are NOT here; they're fetched at
    # boot by bot-fetch-secrets.service and appended to the env file.
    discord_bot_name             = var.discord_bot_name
    discord_secret_path          = var.discord_secret_path
    discord_guild_id             = var.discord_guild_id
    music_command_channel_id     = var.music_command_channel_id
    gcp_project_id               = var.project_id
    valheim_instance_name        = var.valheim_instance_name
    valheim_zone                 = var.valheim_zone
    valheim_status_http_port     = var.valheim_status_http_port
    valheim_password_secret_path = var.valheim_password_secret_path
    lavalink_port                = var.lavalink_port

    # Secret IDs for the bot-fetch-secrets script. The script
    # constructs the full versioned path from these.
    lavalink_password_secret_id   = var.lavalink_password_secret_id
    spotify_credentials_secret_id = var.spotify_credentials_secret_id

    # Bot deploy mechanism.
    bot_git_repo = var.bot_git_repo
    bot_git_ref  = var.bot_git_ref
  })
}

resource "google_compute_instance" "bot_vm" {
  project      = var.project_id
  name         = "bot-vm"
  machine_type = var.machine_type
  zone         = var.zone

  # `bot-vm` tag matches the firewall rule target_tags.
  tags = ["bot-vm"]

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

    # Public IP for outbound to Discord + GCP APIs. Inbound is gated
    # by the firewall (IAP SSH only). The IP is ephemeral; the bot
    # doesn't expose a stable endpoint anyone needs to reach.
    access_config {}
  }

  service_account {
    email  = var.service_account_email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = {
    startup-script         = local.startup_script
    enable-oslogin         = "FALSE"
    block-project-ssh-keys = "TRUE"
  }

  allow_stopping_for_update = true

  lifecycle {
    # gcloud compute ssh adds the user's SSH key to instance metadata
    # automatically; without this ignore, every TF plan after an SSH
    # would show drift wanting to remove the key.
    ignore_changes = [
      metadata["ssh-keys"],
    ]
  }
}
