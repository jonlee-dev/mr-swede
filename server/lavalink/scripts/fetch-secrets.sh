#!/usr/bin/env bash
# Fetch the Lavalink server password from GCP Secret Manager via the
# instance metadata server, then write it to /etc/lavalink/secret.env
# so docker-compose picks it up via env_file.
#
# Runs as a oneshot systemd unit (lavalink-fetch-secrets.service)
# BEFORE lavalink.service starts. Failing this unit fails the
# dependent service, which is what we want -- starting Lavalink with
# the default password "changeme" would silently allow any
# unauthenticated client on the open internet to drive playback.

set -euo pipefail

SECRET_NAME="${SECRET_NAME:-lavalink-server-password}"
SECRET_FILE="/etc/lavalink/secret.env"
META="http://metadata.google.internal/computeMetadata/v1"
HDR="Metadata-Flavor: Google"

PROJECT_ID="$(curl -fsS -H "$HDR" "$META/project/project-id")"
ACCESS_TOKEN="$(curl -fsS -H "$HDR" "$META/instance/service-accounts/default/token" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"

PAYLOAD_B64="$(curl -fsS -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://secretmanager.googleapis.com/v1/projects/${PROJECT_ID}/secrets/${SECRET_NAME}/versions/latest:access" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["payload"]["data"])')"

PASSWORD="$(printf '%s' "$PAYLOAD_B64" | base64 -d)"

# Lavalink will accept any password but a short one defeats the
# purpose. Fail loudly here so it's caught at boot, not when the bot
# starts trying to log into a wide-open server.
if [[ ${#PASSWORD} -lt 16 ]]; then
  echo "ERROR: lavalink password from Secret Manager is shorter than 16 chars" >&2
  exit 1
fi

install -d -m 0750 -o root -g root /etc/lavalink
install -m 0600 -o root -g root /dev/null "$SECRET_FILE"
printf 'LAVALINK_SERVER_PASSWORD=%s\n' "$PASSWORD" > "$SECRET_FILE"
