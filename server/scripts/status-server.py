#!/usr/bin/env python3
"""Log-scraping status server for the Valheim VM.

Tails `docker compose logs` periodically, parses the lloesche image's
output for join-code + player-count events, and serves the result as
JSON at GET :9001/status.json.

Why log scraping and not Steam A2S?
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
      "player_count": <int>,           # 0 if no recent connect/disconnect events
      "server_running": <bool>,        # tracks "Game server connected" / "OnApplicationQuit"
      "error": "<repr>"                # only present when the last scrape failed
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
LOG_TAIL = 500
REFRESH_SECONDS = 30
SUBPROCESS_TIMEOUT = 20

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
}
_state_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scrape_once() -> None:
    """Run `docker compose logs` once and refresh _state from the latest matches."""
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                COMPOSE_FILE,
                "logs",
                "--tail",
                str(LOG_TAIL),
            ],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        log.warning("docker logs timed out: %r", exc)
        with _state_lock:
            _state["last_update"] = _now_iso()
            _state["error"] = repr(exc)
        return

    join_code: str | None = None
    player_count = 0
    server_running = False

    # Iterate forward; later matches overwrite earlier ones, so we end
    # up with the most recent value of each.
    for line in result.stdout.splitlines():
        if (m := JOIN_CODE_RE.search(line)) is not None:
            join_code = m.group(1)
        if (m := PLAYER_COUNT_RE.search(line)) is not None:
            player_count = int(m.group(1))
        if SERVER_UP_RE.search(line):
            server_running = True
        if SERVER_DOWN_RE.search(line):
            server_running = False

    with _state_lock:
        _state.update(
            {
                "last_update": _now_iso(),
                "join_code": join_code,
                "player_count": player_count,
                "server_running": server_running,
            }
        )
        _state.pop("error", None)
    log.info(
        "scrape ok: join_code=%s player_count=%d server_running=%s",
        join_code,
        player_count,
        server_running,
    )


def _scrape_loop() -> None:
    """Background loop: refresh state every REFRESH_SECONDS forever."""
    while True:
        try:
            _scrape_once()
        except Exception as exc:  # noqa: BLE001 -- broad on purpose; loop must not die
            log.exception("Unexpected scrape error")
            with _state_lock:
                _state["error"] = repr(exc)
                _state["last_update"] = _now_iso()
        time.sleep(REFRESH_SECONDS)


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
    threading.Thread(target=_scrape_loop, daemon=True).start()
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
