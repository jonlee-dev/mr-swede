#!/usr/bin/env bash
# Fetch Lavalink-side secrets from GCP Secret Manager via the instance
# metadata server, then write them to /etc/lavalink/secret.env so
# lavalink.service picks them up via EnvironmentFile=.
#
# Three secrets handled here:
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
#   3. lavalink-youtube-oauth-token (OPTIONAL, bootstrap-then-required)
#      A Google OAuth refresh token that the youtube-source plugin
#      uses to authenticate as a real user against YouTube, bypassing
#      anti-bot rollouts. ALWAYS enable OAuth (LAVALINK_OAUTH_ENABLED=true);
#      if no token is seeded yet, leave LAVALINK_OAUTH_REFRESH_TOKEN
#      empty and the plugin will print a device-code URL on boot for
#      the operator to complete the one-time auth dance. After the
#      operator seeds the resulting token, subsequent boots load it
#      silently. See docs/runbook.md scenario 20.
#
# Runs as a oneshot systemd unit (lavalink-fetch-secrets.service)
# BEFORE lavalink.service starts.

set -euo pipefail

PASSWORD_SECRET_NAME="${PASSWORD_SECRET_NAME:-lavalink-server-password}"
SPOTIFY_SECRET_NAME="${SPOTIFY_SECRET_NAME:-spotify-client-credentials}"
OAUTH_SECRET_NAME="${OAUTH_SECRET_NAME:-lavalink-youtube-oauth-token}"
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

# ---------------------------------------------------------------------------
# 3. YouTube OAuth refresh token (optional bootstrap-then-required).
#    Always emit LAVALINK_OAUTH_ENABLED=true; the token field is empty
#    on first boot so the plugin enters device-code mode, then seeded
#    via GSM after the operator completes the one-time auth.
# ---------------------------------------------------------------------------

printf 'LAVALINK_OAUTH_ENABLED=true\n' >> "$SECRET_FILE"

OAUTH_TMP="$(mktemp)"
OAUTH_HTTP_CODE="$(curl -sS -o "$OAUTH_TMP" -w '%{http_code}' \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://secretmanager.googleapis.com/v1/projects/${PROJECT_ID}/secrets/${OAUTH_SECRET_NAME}/versions/latest:access" || true)"

if [[ "$OAUTH_HTTP_CODE" == "200" ]]; then
  OAUTH_TOKEN="$(python3 -c 'import json,sys,base64; d=json.load(open(sys.argv[1])); print(base64.b64decode(d["payload"]["data"]).decode("utf-8").strip())' "$OAUTH_TMP")"
  if [[ -z "$OAUTH_TOKEN" ]]; then
    echo "WARN: YouTube OAuth secret has an empty payload -- entering device-code flow on boot." >&2
    printf 'LAVALINK_OAUTH_REFRESH_TOKEN=\n' >> "$SECRET_FILE"
  else
    # Real token loaded. We DON'T log a length or any portion of the
    # token (a portion of a refresh token is enough to attempt replay).
    echo "Loaded YouTube OAuth refresh token from Secret Manager."
    printf 'LAVALINK_OAUTH_REFRESH_TOKEN=%s\n' "$OAUTH_TOKEN" >> "$SECRET_FILE"
  fi
elif [[ "$OAUTH_HTTP_CODE" == "404" ]]; then
  # Either the secret container doesn't exist yet (pre-TF-apply) OR
  # the container exists but has no versions (post-TF-apply, before
  # the operator completes the device-code flow). Either way, leave
  # the token empty so the plugin enters device-code mode.
  echo "WARN: YouTube OAuth secret not seeded -- Lavalink will print a device-code URL on boot. See docs/runbook.md scenario 20." >&2
  printf 'LAVALINK_OAUTH_REFRESH_TOKEN=\n' >> "$SECRET_FILE"
else
  # Hard failure -- 403 (IAM missing), 5xx, etc. We DON'T want to
  # silently boot Lavalink in degraded mode because that would just
  # keep the user broken without surfacing why. Fail loudly here.
  echo "ERROR: failed to fetch YouTube OAuth secret (HTTP $OAUTH_HTTP_CODE):" >&2
  cat "$OAUTH_TMP" >&2 || true
  rm -f "$OAUTH_TMP"
  exit 1
fi

rm -f "$OAUTH_TMP"
