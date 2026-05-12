#!/usr/bin/env bash
# Liveness probe for the bot, equivalent to Cloud Run's
# `liveness_probe` configuration on the old mr-swede service.
#
# Curls localhost:8080/livez. Counts consecutive failures via a
# tiny state file at /var/lib/bot-watchdog/consecutive-failures.
# After 5 consecutive non-200 responses, calls `systemctl restart
# bot` and resets the counter.
#
# Why this exists (the 2026-05-08 incident shape):
#   The bot's Discord gateway WS can silently degrade -- ws.open
#   stays True briefly, bot.is_ready() never resets, and the bot
#   doesn't crash. systemd's Restart=always wouldn't catch it
#   because the process is alive. /livez detects the degradation
#   (no recent gateway message, or ws closed); 5 consecutive 503s
#   give us a `systemctl restart` which clears in-process state
#   and reconnects fresh.
#
# Same threshold + period semantics as the Cloud Run probe we ran
# from 2026-05-08 to 2026-05-10: period 60s (the timer fires every
# 60s; this script runs once per fire), 5 consecutive failures
# before action. ~5 min unhealthy before restart -- short enough
# to recover without operator intervention, long enough to ride
# out a transient blip.

set -euo pipefail

STATE_DIR="/var/lib/bot-watchdog"
STATE_FILE="$STATE_DIR/consecutive-failures"
FAILURE_THRESHOLD=5
PROBE_URL="http://localhost:8080/livez"
PROBE_TIMEOUT_SECONDS=3

mkdir -p "$STATE_DIR"
[ -f "$STATE_FILE" ] || echo 0 > "$STATE_FILE"

failures="$(cat "$STATE_FILE")"

# `-o /dev/null -w '%{http_code}'` gives us just the status code.
# `-m` is a hard timeout per the configured value; if the bot is
# wedged the probe should never block forever.
http_code="$(curl -sS -m "$PROBE_TIMEOUT_SECONDS" -o /dev/null \
  -w '%{http_code}' "$PROBE_URL" 2>/dev/null || echo "000")"

if [ "$http_code" = "200" ]; then
  # Healthy. Reset counter on success so we don't accumulate
  # failures across long uptime windows.
  if [ "$failures" != "0" ]; then
    echo "[watchdog] /livez OK (was $failures consecutive failures); reset"
    echo 0 > "$STATE_FILE"
  fi
  exit 0
fi

# Probe failed (any non-200 including timeout/connection refused).
failures=$((failures + 1))
echo "$failures" > "$STATE_FILE"
echo "[watchdog] /livez failed (http=$http_code), consecutive=$failures/$FAILURE_THRESHOLD"

if [ "$failures" -ge "$FAILURE_THRESHOLD" ]; then
  echo "[watchdog] threshold reached -> restarting bot.service"
  # Restart asynchronously: this watchdog runs as a oneshot, no
  # need to wait. systemctl restart is itself synchronous, but a
  # bot stop+start can take 30-60s and blocking the watchdog
  # timer that long isn't useful.
  systemctl restart bot.service
  # Reset the counter so we don't immediately re-trigger if the
  # restarting bot returns 503s during its boot window.
  echo 0 > "$STATE_FILE"
fi
