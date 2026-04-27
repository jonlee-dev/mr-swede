"""Idle watcher: stop the Valheim VM after N consecutive empty checks.

Cloud Scheduler invokes this HTTP function on a schedule (default
every 30 minutes). On each tick we:

  1. Read VM state. If status != RUNNING, reset the counter and no-op.
     Manual `/valheim stop` runs through here too -- the watcher
     won't fight a user-initiated stop.
  2. Fetch /status.json from the VM's log-scraping daemon. If the
     fetch fails (timeout, unreachable, malformed), conservatively
     no-op WITHOUT incrementing -- a transient blip on the public
     internet shouldn't count as "no players."
  3. If the daemon reports >0 players, reset the counter.
  4. If the daemon reports 0 players, increment the counter. If it
     reaches EMPTY_CHECKS_TO_STOP, issue instances.stop and reset.

Why HTTP and not Steam A2S?
    Valheim's crossplay/PlayFab transport made legacy Steam A2S
    queries unreliable (the dedicated server doesn't respond to
    standard A2S anymore). server/scripts/status-server.py on the VM
    scrapes `docker compose logs` for live state and exposes it as
    JSON; that's what we consume here.

State (one int, "consecutive_empty") lives in a tiny GCS JSON object
so the function stays stateless and we don't need Firestore.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

import functions_framework
from google.cloud import compute_v1, storage

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

PROJECT = os.environ["GCP_PROJECT"]
ZONE = os.environ["VALHEIM_ZONE"]
INSTANCE = os.environ["VALHEIM_INSTANCE_NAME"]
STATUS_HTTP_PORT = int(os.environ["VALHEIM_STATUS_HTTP_PORT"])
STATE_BUCKET = os.environ["IDLE_WATCHER_STATE_BUCKET"]
STATE_OBJECT = os.environ.get("IDLE_WATCHER_STATE_OBJECT", "state.json")
EMPTY_CHECKS_TO_STOP = int(os.environ["IDLE_WATCHER_EMPTY_CHECKS_TO_STOP"])
STATUS_HTTP_TIMEOUT_SECONDS = float(os.environ.get("VALHEIM_STATUS_HTTP_TIMEOUT_SECONDS", "5.0"))


@functions_framework.http
def check_and_stop(request):  # noqa: ARG001 -- HTTP framework requires the param
    """HTTP entry point. Always returns 200 with a human-readable body."""
    instances = compute_v1.InstancesClient()
    storage_client = storage.Client()
    blob = storage_client.bucket(STATE_BUCKET).blob(STATE_OBJECT)

    vm = instances.get(project=PROJECT, zone=ZONE, instance=INSTANCE)
    if vm.status != "RUNNING":
        _write_state(blob, 0)
        msg = f"VM status is {vm.status}, no-op"
        logger.info(msg)
        return msg, 200

    public_ip = _public_ip(vm)
    if not public_ip:
        # VM is RUNNING but the network interface hasn't surfaced a
        # public IP yet -- can happen during STAGING -> RUNNING window.
        msg = "VM is RUNNING but has no public IP yet, no-op"
        logger.warning(msg)
        return msg, 200

    player_count = _query_player_count(public_ip)
    if player_count is None:
        # Conservative: any fetch failure does NOT count as empty.
        msg = "status.json fetch failed, no-op"
        logger.warning(msg)
        return msg, 200

    state = _read_state(blob)
    if player_count > 0:
        _write_state(blob, 0)
        msg = f"{player_count} player(s) online, reset counter"
        logger.info(msg)
        return msg, 200

    new_count = state.get("consecutive_empty", 0) + 1
    if new_count >= EMPTY_CHECKS_TO_STOP:
        instances.stop(project=PROJECT, zone=ZONE, instance=INSTANCE)
        _write_state(blob, 0)
        msg = f"Stopped VM after {new_count} consecutive empty checks"
        logger.info(msg)
        return msg, 200

    _write_state(blob, new_count)
    msg = f"0 players online, empty count {new_count}/{EMPTY_CHECKS_TO_STOP}"
    logger.info(msg)
    return msg, 200


def _public_ip(vm: Any) -> str | None:
    for nic in vm.network_interfaces or []:
        for ac in nic.access_configs or []:
            if ac.nat_i_p:
                return ac.nat_i_p
    return None


def _query_player_count(public_ip: str) -> int | None:
    """Fetch /status.json from the VM. Returns player_count, or None on any failure.

    None means "we couldn't determine player count" and the caller
    treats it conservatively (does not count as empty). The daemon's
    own `error` field also returns None to keep the watcher from
    auto-stopping on stale data.
    """
    url = f"http://{public_ip}:{STATUS_HTTP_PORT}/status.json"
    try:
        with urllib.request.urlopen(url, timeout=STATUS_HTTP_TIMEOUT_SECONDS) as resp:  # noqa: S310 -- http allowed; payload non-sensitive
            data = json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("status.json fetch failed (%s): %r", url, exc)
        return None

    if data.get("error"):
        logger.warning("status.json reports daemon error: %r", data["error"])
        return None
    try:
        return int(data.get("player_count", 0))
    except (TypeError, ValueError) as exc:
        logger.warning("status.json player_count not parseable: %r", exc)
        return None


def _read_state(blob: Any) -> dict:
    """Return the state dict, or a fresh-start default if the object doesn't exist."""
    try:
        return json.loads(blob.download_as_text())
    except Exception as exc:  # noqa: BLE001 -- Not-Found is expected on first run
        logger.info("State read fallback: %r", exc)
        return {"consecutive_empty": 0}


def _write_state(blob: Any, consecutive_empty: int) -> None:
    payload = json.dumps({"consecutive_empty": consecutive_empty})
    blob.upload_from_string(payload, content_type="application/json")
