#!/usr/bin/env bash
# Fetch the Valheim server password from GCP Secret Manager via the
# instance metadata server, then write it to /etc/valheim/secret.env so
# docker-compose can pick it up via env_file.
#
# Runs as a oneshot systemd unit (valheim-fetch-secrets.service) BEFORE
# valheim.service starts. Failing this unit fails the dependent service,
# which is the behavior we want -- starting Valheim with no password
# would silently start an open server.

set -euo pipefail

SECRET_NAME="${SECRET_NAME:-valheim-server-password}"
SECRET_FILE="/etc/valheim/secret.env"
META="http://metadata.google.internal/computeMetadata/v1"
HDR="Metadata-Flavor: Google"

PROJECT_ID="$(curl -fsS -H "$HDR" "$META/project/project-id")"
ACCESS_TOKEN="$(curl -fsS -H "$HDR" "$META/instance/service-accounts/default/token" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"

PAYLOAD_B64="$(curl -fsS -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://secretmanager.googleapis.com/v1/projects/${PROJECT_ID}/secrets/${SECRET_NAME}/versions/latest:access" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["payload"]["data"])')"

PASSWORD="$(printf '%s' "$PAYLOAD_B64" | base64 -d)"

# Valheim refuses passwords shorter than 5 characters; fail loudly here
# rather than letting the container loop on startup.
if [[ ${#PASSWORD} -lt 5 ]]; then
  echo "ERROR: server password from Secret Manager is shorter than 5 chars" >&2
  exit 1
fi

install -d -m 0750 -o root -g root /etc/valheim
install -m 0600 -o root -g root /dev/null "$SECRET_FILE"
printf 'SERVER_PASS=%s\n' "$PASSWORD" > "$SECRET_FILE"
