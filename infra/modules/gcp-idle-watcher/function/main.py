"""Idle watcher: stop the Valheim VM after N consecutive empty A2S checks.

Cloud Scheduler invokes this HTTP function on a schedule (default every
30 minutes). On each tick we:

  1. Read VM state. If status != RUNNING, reset the counter and no-op.
     Manual `/valheim stop` runs through here too -- the watcher won't
     fight a user-initiated stop.
  2. Probe Valheim's A2S query port. If the probe fails (timeout,
     unreachable), conservatively no-op WITHOUT incrementing -- a
     missed packet on the public internet shouldn't count as "no
     players."
  3. If A2S reports >0 players, reset the counter.
  4. If A2S reports 0 players, increment the counter. If the counter
     reaches EMPTY_CHECKS_TO_STOP, issue instances.stop and reset the
     counter.

State (just a single integer, "consecutive_empty") lives in a tiny GCS
JSON object so the function stays stateless and we don't have to bring
back Firestore for this one feature.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import a2s
import functions_framework
from google.cloud import compute_v1, storage

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

PROJECT = os.environ["GCP_PROJECT"]
ZONE = os.environ["VALHEIM_ZONE"]
INSTANCE = os.environ["VALHEIM_INSTANCE_NAME"]
A2S_PORT = int(os.environ["VALHEIM_A2S_PORT"])
STATE_BUCKET = os.environ["IDLE_WATCHER_STATE_BUCKET"]
STATE_OBJECT = os.environ.get("IDLE_WATCHER_STATE_OBJECT", "state.json")
EMPTY_CHECKS_TO_STOP = int(os.environ["IDLE_WATCHER_EMPTY_CHECKS_TO_STOP"])
A2S_TIMEOUT_SECONDS = float(os.environ.get("VALHEIM_A2S_TIMEOUT_SECONDS", "5.0"))


@functions_framework.http
def check_and_stop(request):  # noqa: ARG001  -- HTTP framework requires the param
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

    try:
        info = a2s.info((public_ip, A2S_PORT), timeout=A2S_TIMEOUT_SECONDS)
        player_count = info.player_count
    except Exception as exc:  # noqa: BLE001  -- conservative on any A2S failure
        # Conservative: probe failures don't count as empty.
        msg = f"A2S query to {public_ip}:{A2S_PORT} failed ({exc!r}), no-op"
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
