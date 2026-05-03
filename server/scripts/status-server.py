#!/usr/bin/env python3
"""Steam A2S query daemon for the Valheim VM.

Periodically queries the Valheim dedicated server's UDP query port
(2457 by Valheim convention = game_port + 1) for an A2S_INFO response,
parses out the live player count, and serves the result as JSON at
GET :9001/status.json.

Why A2S directly and not log scraping?
    The previous "tail docker compose logs" approach (both the
    initial polling version AND the 2026-05-02 follow-stream rewrite)
    repeatedly missed real player events. The follow-stream version
    in particular suffered from `docker compose logs --follow`
    exiting with code 0 within 1 second of attaching during VM boot
    races and at random times during normal runtime. Reconnects took
    5+ seconds each, and any join/leave events that fired in those
    gaps were lost. The watcher then false-stopped active sessions.

    A2S is the canonical Steam Server Browser query protocol --
    the game itself answers it, no log parsing involved. Earlier we
    abandoned A2S because Valheim's crossplay/PlayFab transport made
    queries unreliable; with `CROSSPLAY=false` (the 2026-05-02 fix
    for unrelated PlayFab lag) the protocol responds correctly.
    Verified: a manual A2S_INFO probe to the live server returned a
    valid response with the correct player count.

Why stdlib socket and not the python-a2s library?
    Debian 12's PEP 668 makes system pip installs awkward (would
    require --break-system-packages or a venv). The A2S_INFO query
    is ~30 lines of Python with stdlib `socket` and byte-slicing.
    Reinventing this much wheel is cheaper than the pip-install
    plumbing in startup-script.

Output schema (UNCHANGED from prior daemon -- the bot's
src.services.server_query and the idle-watcher's _probe_valheim
both consume this format and need no changes):

    {
      "last_update": "<ISO8601 UTC>",
      "join_code": null,           # always null now -- A2S doesn't
                                   # expose PlayFab join codes, and
                                   # with CROSSPLAY=false there is no
                                   # PlayFab join code at all. Bot's
                                   # /valheim status embed has a
                                   # "How to join" branch for this.
      "player_count": <int>,
      "server_running": <bool>,    # true iff the most recent A2S
                                   # query succeeded
      "error": "<repr>"            # only present when the most recent
                                   # query failed; the idle-watcher
                                   # already treats this as "unknown"
                                   # and does NOT increment its empty
                                   # counter, which is the safe path
    }

Stdlib only. Runs as the valheim-status systemd unit; binds 0.0.0.0:9001.
"""

from __future__ import annotations

import json
import logging
import socket
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

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

HTTP_PORT = 9001
QUERY_HOST = "127.0.0.1"
QUERY_PORT = 2457  # Valheim convention: game_port (2456) + 1
QUERY_INTERVAL_SECONDS = 30
QUERY_TIMEOUT_SECONDS = 3.0

# ---------------------------------------------------------------------------
# Steam A2S protocol -- the bare slice we need.
#
# Reference: https://developer.valvesoftware.com/wiki/Server_queries
#
# Wire format:
#   1. Send A2S_INFO_REQUEST (28 bytes).
#   2. Modern Source servers (since 2020) reply with S2C_CHALLENGE
#      containing a 4-byte token; resend the request with the token
#      appended (32 bytes total).
#   3. The server then replies with S2A_INFO_SRC: 4-byte header,
#      type byte 'I' (0x49), 1-byte protocol, four null-terminated
#      strings (server name, map, folder, game), 2-byte short app id,
#      then the bytes we actually care about: players, max_players,
#      bots, server_type, environment, visibility, vac, version (NTS),
#      optional EDF flagged extra fields.
#
# We only read up to player_count + max_players. Everything after that
# is ignored.
# ---------------------------------------------------------------------------

_A2S_HEADER = b"\xff\xff\xff\xff"
_A2S_INFO_QUERY_BODY = b"TSource Engine Query\x00"
_A2S_INFO_REQUEST = _A2S_HEADER + _A2S_INFO_QUERY_BODY
_A2S_RESP_TYPE_CHALLENGE = 0x41  # 'A'
_A2S_RESP_TYPE_INFO = 0x49  # 'I'


def _query_a2s_info(
    host: str, port: int, timeout: float
) -> tuple[int, int] | None:
    """Send an A2S_INFO query and return (player_count, max_players).

    Handles the challenge handshake transparently. Returns None on any
    error (timeout, malformed response, EOF-before-required-fields).
    Failures are logged at WARNING -- the caller should set error
    state, not retry, and rely on the next periodic tick.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        try:
            sock.sendto(_A2S_INFO_REQUEST, (host, port))
            data, _ = sock.recvfrom(4096)
        except (socket.timeout, OSError) as exc:
            log.warning("A2S send/recv failed (host=%s:%d): %r", host, port, exc)
            return None

        # If the server responded with a challenge, re-send the query
        # with the 4-byte challenge token appended.
        if (
            len(data) >= 9
            and data[0:4] == _A2S_HEADER
            and data[4] == _A2S_RESP_TYPE_CHALLENGE
        ):
            challenge = data[5:9]
            try:
                sock.sendto(_A2S_INFO_REQUEST + challenge, (host, port))
                data, _ = sock.recvfrom(4096)
            except (socket.timeout, OSError) as exc:
                log.warning("A2S challenge resend failed: %r", exc)
                return None

        # Expect S2A_INFO_SRC: \xFFx4 + 'I' + ...
        if len(data) < 6 or data[0:4] != _A2S_HEADER or data[4] != _A2S_RESP_TYPE_INFO:
            log.warning(
                "A2S response not S2A_INFO_SRC; first bytes=%s len=%d",
                data[:8].hex(),
                len(data),
            )
            return None

        # Skip 4-byte header, type byte, protocol byte.
        pos = 6
        try:
            # Four NUL-terminated strings: name, map, folder, game.
            for _ in range(4):
                end = data.index(b"\x00", pos)
                pos = end + 1
            # 2-byte short app ID, then the byte we want: players.
            if len(data) < pos + 2 + 2:
                log.warning("A2S response truncated before player count")
                return None
            pos += 2
            return data[pos], data[pos + 1]
        except (ValueError, IndexError) as exc:
            log.warning("A2S response parse error: %r", exc)
            return None
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Daemon state and query loop
# ---------------------------------------------------------------------------

# Initial player_count is 0 -- the watcher only stops a VM after N
# CONSECUTIVE empty reads, AND only when state.error is absent. So
# even if the daemon serves /status.json before its first query
# completes, the watcher won't act on it (server_running=false, no
# error field set initially -- but the watcher also checks error;
# we set error on the first failure, which makes the unknown-state
# explicit).
_state: dict[str, Any] = {
    "last_update": None,
    "join_code": None,
    "player_count": 0,
    "server_running": False,
}
_state_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _query_loop() -> None:
    """Run an A2S_INFO query every QUERY_INTERVAL_SECONDS forever.

    On query failure, we set the `error` field and `server_running` to
    False. We DO NOT reset player_count -- the watcher checks `error`
    first and treats it as 'unknown' (no counter increment), so a
    stale player_count value can't drive a false stop. Keeping the
    last-known player_count means the bot's /valheim status doesn't
    flap to 0 during transient query blips either.

    On success, we update player_count from the response, clear the
    error field, and mark server_running True.
    """
    while True:
        result = _query_a2s_info(QUERY_HOST, QUERY_PORT, QUERY_TIMEOUT_SECONDS)
        with _state_lock:
            if result is None:
                _state["server_running"] = False
                # Surface why the query failed so the watcher's error
                # check fires. The exact reason is in the journal at
                # WARNING level; the JSON value is just a short tag.
                _state["error"] = "a2s_query_failed"
            else:
                players, _max_players = result
                _state["player_count"] = int(players)
                _state["server_running"] = True
                _state.pop("error", None)
            _state["last_update"] = _now_iso()
        time.sleep(QUERY_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# HTTP server (unchanged surface from prior daemon).
# ---------------------------------------------------------------------------


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
        # Silence per-request logging; the query loop is what we care
        # about for ops, and that already logs failures at WARNING.
        return


def main() -> None:
    log.info(
        "Valheim status server starting (HTTP :%d, A2S target %s:%d, interval %ds)",
        HTTP_PORT,
        QUERY_HOST,
        QUERY_PORT,
        QUERY_INTERVAL_SECONDS,
    )
    threading.Thread(target=_query_loop, daemon=True).start()
    server = HTTPServer(("0.0.0.0", HTTP_PORT), _Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
