#!/usr/bin/env bash
# Fetch Lavalink-side secrets from GCP Secret Manager via the instance
# metadata server, then write them to /etc/lavalink/secret.env so
# lavalink.service picks them up via EnvironmentFile=.
#
# Two secrets handled here:
#
#   1. lavalink-server-password (REQUIRED)
#      A short bytestring used as the password the bot authenticates
#      with against Lavalink's REST + WS endpoints. Failure to fetch
#      this fails the unit and blocks Lavalink from starting --
#      starting Lavalink with the default password "changeme" would
#      silently allow any unauthenticated client on the open internet
#      to drive playback.
#
#   2. spotify-client-credentials (OPTIONAL)
#      JSON {"client_id": "...", "client_secret": "..."} consumed by
#      the lavasrc plugin to resolve Spotify URLs. If the secret has
#      no version yet (user hasn't seeded after first apply), or if
#      the secret container itself is missing, we log a warning,
#      write LAVASRC_SPOTIFY_ENABLED=false, and continue. Lavalink
#      will boot and serve YouTube/HTTP traffic; Spotify URLs will
#      error cleanly until the user seeds the credentials.
#
# Runs as a oneshot systemd unit (lavalink-fetch-secrets.service)
# BEFORE lavalink.service starts.

set -euo pipefail

PASSWORD_SECRET_NAME="${PASSWORD_SECRET_NAME:-lavalink-server-password}"
SPOTIFY_SECRET_NAME="${SPOTIFY_SECRET_NAME:-spotify-client-credentials}"
SECRET_FILE="/etc/lavalink/secret.env"
META="http://metadata.google.internal/computeMetadata/v1"
HDR="Metadata-Flavor: Google"

PROJECT_ID="$(curl -fsS -H "$HDR" "$META/project/project-id")"
ACCESS_TOKEN="$(curl -fsS -H "$HDR" "$META/instance/service-accounts/default/token" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"

# ---------------------------------------------------------------------------
# 1. Lavalink server password (required).
# ---------------------------------------------------------------------------

PAYLOAD_B64="$(curl -fsS -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://secretmanager.googleapis.com/v1/projects/${PROJECT_ID}/secrets/${PASSWORD_SECRET_NAME}/versions/latest:access" \
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

# ---------------------------------------------------------------------------
# 2. Spotify Developer credentials (optional). If absent, lavasrc's
#    Spotify source stays disabled; Lavalink boots fine without it.
# ---------------------------------------------------------------------------

SPOTIFY_TMP="$(mktemp)"
SPOTIFY_HTTP_CODE="$(curl -sS -o "$SPOTIFY_TMP" -w '%{http_code}' \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://secretmanager.googleapis.com/v1/projects/${PROJECT_ID}/secrets/${SPOTIFY_SECRET_NAME}/versions/latest:access" || true)"

if [[ "$SPOTIFY_HTTP_CODE" == "200" ]]; then
  # Inner: {"payload": {"data": "<base64>"}} where base64 decodes to
  # the user-supplied JSON {"client_id": "...", "client_secret": "..."}.
  INNER_JSON="$(python3 -c 'import json,sys,base64; d=json.load(open(sys.argv[1])); print(base64.b64decode(d["payload"]["data"]).decode("utf-8"))' "$SPOTIFY_TMP")"
  CLIENT_ID="$(printf '%s' "$INNER_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["client_id"])')"
  CLIENT_SECRET="$(printf '%s' "$INNER_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["client_secret"])')"

  if [[ -z "$CLIENT_ID" || -z "$CLIENT_SECRET" ]]; then
    echo "WARN: Spotify credentials secret has empty client_id or client_secret -- disabling lavasrc Spotify source." >&2
    printf 'LAVASRC_SPOTIFY_ENABLED=false\n' >> "$SECRET_FILE"
  else
    printf 'LAVASRC_SPOTIFY_ENABLED=true\n' >> "$SECRET_FILE"
    printf 'LAVASRC_SPOTIFY_CLIENT_ID=%s\n' "$CLIENT_ID" >> "$SECRET_FILE"
    printf 'LAVASRC_SPOTIFY_CLIENT_SECRET=%s\n' "$CLIENT_SECRET" >> "$SECRET_FILE"
  fi
elif [[ "$SPOTIFY_HTTP_CODE" == "404" ]]; then
  echo "WARN: Spotify credentials secret has no versions yet -- disabling lavasrc Spotify source. Seed with: gcloud secrets versions add ${SPOTIFY_SECRET_NAME} --data-file=-" >&2
  printf 'LAVASRC_SPOTIFY_ENABLED=false\n' >> "$SECRET_FILE"
else
  echo "ERROR: failed to fetch Spotify credentials (HTTP $SPOTIFY_HTTP_CODE):" >&2
  cat "$SPOTIFY_TMP" >&2 || true
  rm -f "$SPOTIFY_TMP"
  exit 1
fi

rm -f "$SPOTIFY_TMP"
