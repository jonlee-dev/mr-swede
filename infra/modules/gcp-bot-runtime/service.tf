###############################################################################
# Cloud Run service for the bot.
#
# Why a placeholder image on first apply?
#   google_cloud_run_v2_service requires a non-empty image field. On the
#   FIRST `terraform apply`, no image exists in the AR repo yet -- Cloud
#   Build hasn't run. So we seed the service with cloudrun/hello and let
#   Cloud Build's first push silently replace it.
#
#   `lifecycle.ignore_changes = [template[0].containers[0].image]` is what
#   keeps Terraform from reverting Cloud Build's image updates on every
#   subsequent apply. The image is owned by Cloud Build forever after.
#
# Why these resource limits?
#   See README.md (root) "Cost estimate" section. min/max_instances=1
#   because the bot's Discord gateway WebSocket is single-process; CPU
#   throttling because the bot is mostly idle.
#
# Why no DISCORD_TOKEN env var?
#   The bot reads it from GSM at runtime via DISCORD_SECRET_PATH.
#   Hardcoding the token as a plain env var would put it in the service
#   spec where anyone with run.services.getIamPolicy can read it.
###############################################################################

data "google_project" "current" {
  project_id = var.project_id
}

locals {
  bootstrap_image = "us-docker.pkg.dev/cloudrun/container/hello"

  discord_secret_path           = "projects/${data.google_project.current.number}/secrets/${google_secret_manager_secret.discord_bot_secrets.secret_id}/versions/latest"
  valheim_password_secret_path  = "projects/${data.google_project.current.number}/secrets/${var.valheim_password_secret_id}/versions/latest"
  lavalink_password_secret_path = "projects/${data.google_project.current.number}/secrets/${var.lavalink_password_secret_id}/versions/latest"
}

resource "google_cloud_run_v2_service" "bot" {
  project  = var.project_id
  name     = var.service_name
  location = var.region

  # All ingress is fine -- the HTTP server is just for Cloud Run health
  # probes and a couple of read-only endpoints. Nothing sensitive.
  # Could be tightened to INGRESS_TRAFFIC_INTERNAL_ONLY later if we want
  # to block public probing of /health.
  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.bot.email

    # min/max=1: see variables.tf for why both are mandatory.
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    timeout = "${var.request_timeout_seconds}s"

    containers {
      image = local.bootstrap_image

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }

        # cpu_idle = false means CPU is allocated continuously, not just
        # during request processing. The bot does most of its work
        # OUTSIDE of incoming HTTP requests:
        #   - Discord gateway WebSocket heartbeats every ~41s.
        #   - Slash-command handlers fire from gateway messages, not
        #     from the Cloud Run port.
        #   - Outbound HTTPS calls to compute.googleapis.com from the
        #     /valheim cog -- those need CPU to complete TLS handshakes.
        # Under cpu_idle=true the worker thread doing the TLS handshake
        # gets starved and the connection EOFs mid-handshake.
        # Cost goes from ~$3-5/mo (throttled) to ~$15-20/mo (always-on),
        # but throttled mode is a known anti-pattern for always-on bots.
        cpu_idle          = false
        startup_cpu_boost = true
      }

      ports {
        container_port = 8080
      }

      # Strict liveness probe.
      #
      # The 2026-05-08 incident: bot's Discord gateway WS silently died
      # but bot.is_ready() (sticky once True) and bot.latency (caches
      # last value) both kept reporting "fine" indefinitely. Cloud Run's
      # default TCP probe was satisfied that uvicorn was listening, so
      # the wedged container ran for ~5 hours unable to receive
      # interactions. Each retry on the gateway side burned Discord's
      # IDENTIFY rate-limit budget; eventually we hit a 429 storm.
      #
      # /livez (in src/http.py) returns 503 when ANY of:
      #   - bot is not initialized
      #   - bot.is_ready() is False
      #   - bot.is_closed() is True
      #   - bot.ws is None or not open
      #   - no gateway events received yet
      #   - last gateway event is >90s old (~2 missed heartbeats)
      #
      # Cloud Run hits /livez every 60s; after 5 consecutive 503s
      # (~5 min unhealthy) the container is killed and min-instances=1
      # spins up a fresh replacement. The 5-min grace is intentional:
      # long enough to ride out a transient Cloudflare blip without
      # restart-flapping (which is what burned the IDENTIFY budget on
      # 2026-05-08), short enough that an actual wedge gets fixed
      # without operator intervention.
      # NOTE 2026-05-09: probe path temporarily on /health while we
      # roll out the proper /livez fix (replacing the
      # on_socket_event_type-based freshness signal with
      # bot.ws._keep_alive._last_recv, which doesn't false-positive
      # on quiet bots). Once the bot's new revision is live and we
      # confirm /livez returns 200 reliably, swap path back to
      # "/livez" via a follow-up TF apply.
      liveness_probe {
        http_get {
          path = "/health"
        }
        period_seconds        = 60
        timeout_seconds       = 3
        failure_threshold     = 5
        initial_delay_seconds = 30
      }

      env {
        name  = "ENV"
        value = "production"
      }

      env {
        name  = "LOG_FORMAT"
        value = "json"
      }

      env {
        name  = "DISCORD_BOT_NAME"
        value = var.discord_bot_name
      }

      env {
        name  = "DISCORD_GUILD_ID"
        value = var.discord_guild_id
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "DISCORD_SECRET_PATH"
        value = local.discord_secret_path
      }

      env {
        name  = "VALHEIM_INSTANCE_NAME"
        value = local.vm_name
      }

      env {
        name  = "VALHEIM_ZONE"
        value = local.vm_zone
      }

      env {
        name  = "VALHEIM_STATUS_HTTP_PORT"
        value = tostring(var.valheim_status_http_port)
      }

      env {
        name  = "VALHEIM_PASSWORD_SECRET_PATH"
        value = local.valheim_password_secret_path
      }

      env {
        name  = "LAVALINK_INSTANCE_NAME"
        value = local.lavalink_vm_name
      }

      env {
        name  = "LAVALINK_ZONE"
        value = local.lavalink_vm_zone
      }

      env {
        name  = "LAVALINK_HOST"
        value = "" # resolved at runtime via compute.describe_instance
      }

      env {
        name  = "LAVALINK_PORT"
        value = tostring(var.lavalink_port)
      }

      env {
        name  = "LAVALINK_PASSWORD_SECRET_PATH"
        value = local.lavalink_password_secret_path
      }

      env {
        name  = "MUSIC_COMMAND_CHANNEL_ID"
        value = var.music_command_channel_id
      }
    }
  }

  labels = var.labels

  lifecycle {
    # Cloud Build owns the image and the revision-level labels after
    # bootstrap. Without these ignores:
    #   - every `terraform apply` after a fresh deploy would revert
    #     the running service back to the cloudrun/hello placeholder.
    #   - Cloud Build's per-deploy labels (commit-sha, gcb-build-id,
    #     gcb-trigger-id, managed-by) would tug-of-war with TF: the
    #     deploy adds them, the next plan strips them.
    ignore_changes = [
      template[0].containers[0].image,
      template[0].labels,
      client,
      client_version,
      # Cloud Run v2 has overlapping service-level (manual mode) and
      # template-level (autoscaling) scaling blocks with identical
      # field names. We drive scaling via the template block; the
      # service-level one defaults to {manual=0, min=0} and shows up
      # as endless drift if we don't ignore it.
      scaling,
    ]
  }

  depends_on = [
    google_secret_manager_secret_iam_member.bot_can_read_discord_secrets,
    google_secret_manager_secret_iam_member.bot_can_read_valheim_password,
    google_secret_manager_secret_iam_member.bot_can_read_lavalink_password,
    google_compute_instance_iam_member.bot_can_admin_valheim_vm,
    google_compute_instance_iam_member.bot_can_admin_lavalink_vm,
  ]
}

###############################################################################
# Public invocation (optional).
#
# Cloud Run v2 defaults to private: every request fails with 403 unless
# the caller presents an identity token. That's annoying for smoke tests
# of /health from curl, so we grant `roles/run.invoker` to allUsers by
# default.
#
# What this exposes (all the public endpoints in bot/src/http.py):
#   GET /         → {"status": "ok", "service": "mr-swede", "bot_name": "..."}
#   GET /health   → {"status": "...", "bot_ready": bool, "guilds": N, "latency_ms": F}
#   GET /metrics  → {"guilds": N, "latency_ms": F, "is_ready": bool}
#
# None of those leak identifiable info. Set var.allow_public_invocation
# to false if you'd rather authenticate every probe.
###############################################################################

resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  count = var.allow_public_invocation ? 1 : 0

  project  = var.project_id
  location = google_cloud_run_v2_service.bot.location
  name     = google_cloud_run_v2_service.bot.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
