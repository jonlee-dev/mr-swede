#!/usr/bin/env python3
"""Log-following status server for the Valheim VM.

Tails `docker compose logs --follow` continuously, parses the lloesche
image's output for join-code + player-count + lifecycle events, and
serves the result as JSON at GET :9001/status.json.

Why log-following and not periodic re-scraping?
    The previous implementation polled `docker compose logs --tail 500`
    every 30s and re-derived state from scratch each time. That worked
    when player events were frequent, but lloesche/valheim-server
    emits a lot of routine traffic (saves, network keepalives, GC
    events). After ~15-30 min of uneventful play, the most recent
    "now N player(s)" line scrolled past 500 entries and `player_count`
    fell back to its initial 0. The idle-watcher then logged two
    consecutive empty checks and stopped the VM mid-session.

    The fix is structural: don't re-derive state, MAINTAIN it. We open
    one long-lived `docker compose logs --follow` stream and update
    `_state` as new lines arrive. Player count is now sticky: once
    we've seen the "now N player(s)" event, the value stays in state
    until another event changes it. Same for join_code and
    server_running.

Why log scraping at all (vs Steam A2S)?
    Valheim's crossplay/PlayFab transport replaced legacy Steam A2S as
    the primary discovery mechanism. The dedicated server still binds
    a query port (game_port + 1) but in practice it does not respond
    to standard A2S queries reliably -- both python-a2s and lloesche's
    built-in STATUS_HTTP feature consistently time out. Log scraping
    is the canonical fallback the community uses.

Output schema:
    {
      "last_update": "<ISO8601 UTC timestamp>",
      "join_code": "126828" | null,   # PlayFab join code; null until server registers
      "player_count": <int>,           # 0 until the first "now N player(s)" event
      "server_running": <bool>,        # tracks "Game server connected" / "OnApplicationQuit"
      "stream_alive": <bool>,          # was the docker logs stream open at last update
      "error": "<repr>"                # only present when the stream is currently broken
    }

Stdlib only -- no third-party deps. Runs as the valheim-status systemd
unit; binds 0.0.0.0:9001.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("valheim-status")

PORT = 9001
COMPOSE_FILE = "/opt/valheim/docker-compose.yml"

# When the daemon (re)starts we backfill state from the recent log
# tail so /status.json isn't blank during the first few seconds.
# `--tail all` would be most thorough but on a busy world that's
# 100k+ lines and we don't need that much history -- the latest of
# each event is what we care about. 5000 lines covers ~3-4 hours of
# typical play, more than enough to capture the last "Game server
# connected" + "now N player(s)" + join-code emissions.
INITIAL_TAIL = 5000

# When the docker logs stream dies (docker daemon hiccup, container
# restart, etc.), wait this long before reconnecting. Short enough
# that brief blips don't leave us blind, long enough to avoid a tight
# retry loop if docker is genuinely broken.
RECONNECT_BACKOFF_SECONDS = 5

# Regex patterns sourced from observed `docker compose logs` output of
# lloesche/valheim-server. If lloesche reformats the log lines we
# scrape, only these need updating.
JOIN_CODE_RE = re.compile(r"registered with join code (\d+)")
PLAYER_COUNT_RE = re.compile(r"now (\d+) player\(s\)")
SERVER_UP_RE = re.compile(r"Game server connected")
SERVER_DOWN_RE = re.compile(r"OnApplicationQuit|Server is shutting down")

_state: dict[str, Any] = {
    "last_update": None,
    "join_code": None,
    "player_count": 0,
    "server_running": False,
    "stream_alive": False,
}
_state_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ingest_line(line: str) -> None:
    """Update `_state` based on a single log line.

    Each regex match overwrites the corresponding state field; non-
    matching lines just bump `last_update` so callers can tell the
    stream is alive. State persists across non-matches -- the whole
    point of the rewrite is that an absence of player-count events
    no longer drives player_count back to 0.
    """
    with _state_lock:
        if (m := JOIN_CODE_RE.search(line)) is not None:
            _state["join_code"] = m.group(1)
        if (m := PLAYER_COUNT_RE.search(line)) is not None:
            try:
                _state["player_count"] = int(m.group(1))
            except ValueError:
                # Regex guarantees \d+, but defensive against future
                # pattern edits that might broaden the capture group.
                pass
        if SERVER_UP_RE.search(line):
            _state["server_running"] = True
        if SERVER_DOWN_RE.search(line):
            _state["server_running"] = False
            # Server going down implies no players AND no live join
            # code. Without these resets, a session boundary in the
            # log tail would carry stale values forward:
            #
            #   - player_count -> would mislead the idle-watcher into
            #     thinking the freshly-booted server already has people
            #   - join_code -> would surface a defunct PlayFab code in
            #     /valheim status; particularly bad after a crossplay
            #     toggle because the new server never emits a fresh
            #     "registered with join code" line to overwrite it
            _state["player_count"] = 0
            _state["join_code"] = None
        _state["last_update"] = _now_iso()


def _follow_loop() -> None:
    """Run `docker compose logs --follow` forever, ingesting line-by-line.

    The outer loop handles stream death (docker restart, container
    recreation). On any failure we mark `stream_alive=false`, log the
    cause, sleep the backoff, then reopen. State is preserved across
    reconnects -- we never re-init player_count to 0 just because we
    briefly lost the stream.

    `--tail INITIAL_TAIL` on the first iteration backfills recent
    history so the daemon's first /status.json isn't blank. On
    reconnects we use the same tail value: cheap insurance against
    missing events that occurred during the brief disconnect.
    """
    while True:
        proc: subprocess.Popen[str] | None = None
        try:
            proc = subprocess.Popen(
                [
                    "docker",
                    "compose",
                    "-f",
                    COMPOSE_FILE,
                    "logs",
                    "--follow",
                    "--tail",
                    str(INITIAL_TAIL),
                    "--no-color",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # line-buffered
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("failed to start docker compose logs: %r", exc)
            with _state_lock:
                _state["error"] = repr(exc)
                _state["stream_alive"] = False
                _state["last_update"] = _now_iso()
            time.sleep(RECONNECT_BACKOFF_SECONDS)
            continue

        with _state_lock:
            _state.pop("error", None)
            _state["stream_alive"] = True

        assert proc.stdout is not None
        log.info("docker compose logs --follow attached (pid=%s)", proc.pid)
        try:
            for raw_line in proc.stdout:
                _ingest_line(raw_line.rstrip("\n"))
        except Exception as exc:  # noqa: BLE001 -- broad on purpose; loop must not die
            log.exception("error reading log stream")
            with _state_lock:
                _state["error"] = repr(exc)
        finally:
            with _state_lock:
                _state["stream_alive"] = False
                _state["last_update"] = _now_iso()
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                proc.kill()

        log.warning(
            "docker logs stream ended (exit=%s); reconnecting in %ds",
            proc.returncode,
            RECONNECT_BACKOFF_SECONDS,
        )
        time.sleep(RECONNECT_BACKOFF_SECONDS)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler convention
        if self.path != "/status.json":
            self.send_response(404)
            self.end_headers()
            return
        with _state_lock:
            payload = json.dumps(_state).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache, no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002,ARG002
        # Silence default per-request logging; we log scrape events only.
        return


def main() -> None:
    log.info("Valheim status server starting on :%d", PORT)
    threading.Thread(target=_follow_loop, daemon=True).start()
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
