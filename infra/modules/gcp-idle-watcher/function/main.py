"""Idle watcher: stop on-demand VMs after N consecutive empty checks.

Cloud Scheduler invokes this HTTP function on a schedule (default
every 30 minutes). Each tick we iterate over a list of TARGETS
(currently `valheim` and `lavalink`) and for each:

  1. Read VM state. If status != RUNNING, reset the counter and skip.
     Manual `/valheim stop` / `/music stop` runs through here too --
     the watcher won't fight a user-initiated stop.
  2. Probe the target's idle-check endpoint.
     Different targets, different endpoints:
       - valheim:  HTTP GET <ip>:9001/status.json (log-scraping daemon)
                   -> "active" if json["player_count"] > 0
       - lavalink: HTTP GET <ip>:2333/v4/players (Authorization=password)
                   -> "active" if the array is non-empty
     A probe failure (timeout, unreachable, malformed) is conservatively
     treated as "unknown" -- DOES NOT count as empty.
  3. If "active", reset the counter for this target.
  4. If "empty", increment the counter. If it reaches
     EMPTY_CHECKS_TO_STOP, issue instances.stop and reset.

State is keyed by target name in GCS, one JSON object per target,
so the function stays stateless and we don't need Firestore.

Why poll instead of subscribe to events: simplicity. Cloud Scheduler
+ stateless Cloud Function + GCS state object is the same shape as
the Valheim watcher; adding more targets is adding entries to the
TARGETS list, not new infrastructure.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Callable

import functions_framework
from google.cloud import compute_v1, secretmanager, storage

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

PROJECT = os.environ["GCP_PROJECT"]
STATE_BUCKET = os.environ["IDLE_WATCHER_STATE_BUCKET"]
EMPTY_CHECKS_TO_STOP = int(os.environ["IDLE_WATCHER_EMPTY_CHECKS_TO_STOP"])
PROBE_TIMEOUT_SECONDS = float(os.environ.get("IDLE_WATCHER_PROBE_TIMEOUT_SECONDS", "5.0"))

# Valheim target.
VALHEIM_ZONE = os.environ["VALHEIM_ZONE"]
VALHEIM_INSTANCE = os.environ["VALHEIM_INSTANCE_NAME"]
VALHEIM_STATUS_HTTP_PORT = int(os.environ["VALHEIM_STATUS_HTTP_PORT"])

# Lavalink target. The password is read at function-startup time from
# GSM and held in module scope -- fetching it on every tick would be
# wasteful and the password rotates infrequently.
LAVALINK_ZONE = os.environ["LAVALINK_ZONE"]
LAVALINK_INSTANCE = os.environ["LAVALINK_INSTANCE_NAME"]
LAVALINK_PORT = int(os.environ["LAVALINK_PORT"])
LAVALINK_PASSWORD_SECRET_PATH = os.environ["LAVALINK_PASSWORD_SECRET_PATH"]


def _fetch_secret(path: str) -> str:
    """One-shot read of a GSM secret string. Cached at module load."""
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(request={"name": path})
    return response.payload.data.decode("UTF-8").strip()


# Resolve secret eagerly at cold-start. Cold-start happens at most
# every ~5min on Cloud Functions 2nd-gen with our config; trivial cost.
_LAVALINK_PASSWORD: str | None = None


def _lavalink_password() -> str:
    global _LAVALINK_PASSWORD
    if _LAVALINK_PASSWORD is None:
        _LAVALINK_PASSWORD = _fetch_secret(LAVALINK_PASSWORD_SECRET_PATH)
    return _LAVALINK_PASSWORD


# ---------------------------------------------------------------------------
# Per-target activity probes. Each returns:
#   True  -> server is active, reset counter
#   False -> server is empty, increment counter
#   None  -> unknown / probe failed; do NOT count as empty (conservative)
# ---------------------------------------------------------------------------


def _probe_valheim(public_ip: str) -> bool | None:
    """Hit the Valheim VM's log-scraping daemon at /status.json."""
    url = f"http://{public_ip}:{VALHEIM_STATUS_HTTP_PORT}/status.json"
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_SECONDS) as resp:  # noqa: S310 -- http allowed; payload non-sensitive
            data = json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("valheim status.json fetch failed (%s): %r", url, exc)
        return None

    if data.get("error"):
        logger.warning("valheim status.json reports daemon error: %r", data["error"])
        return None
    try:
        count = int(data.get("player_count", 0))
    except (TypeError, ValueError) as exc:
        logger.warning("valheim player_count not parseable: %r", exc)
        return None
    return count > 0


def _probe_lavalink(public_ip: str) -> bool | None:
    """Hit Lavalink's REST /v4/players. Active = at least one player."""
    url = f"http://{public_ip}:{LAVALINK_PORT}/v4/players"
    req = urllib.request.Request(url, headers={"Authorization": _lavalink_password()})
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SECONDS) as resp:  # noqa: S310 -- http allowed; auth via header
            data = json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        logger.warning("lavalink players fetch failed (%s): %r", url, exc)
        return None

    if not isinstance(data, list):
        logger.warning("lavalink /v4/players returned non-list: %r", type(data))
        return None
    return len(data) > 0


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

# Each target: (name_for_logs+state_key, zone, instance, probe_fn)
TARGETS: list[tuple[str, str, str, Callable[[str], bool | None]]] = [
    ("valheim", VALHEIM_ZONE, VALHEIM_INSTANCE, _probe_valheim),
    ("lavalink", LAVALINK_ZONE, LAVALINK_INSTANCE, _probe_lavalink),
]


@functions_framework.http
def check_and_stop(request):  # noqa: ARG001 -- HTTP framework requires the param
    """HTTP entry point. Iterates over TARGETS, returns a per-target summary."""
    instances = compute_v1.InstancesClient()
    storage_client = storage.Client()
    bucket = storage_client.bucket(STATE_BUCKET)

    summary: list[str] = []
    for name, zone, instance, probe in TARGETS:
        msg = _process_target(instances, bucket, name, zone, instance, probe)
        summary.append(f"[{name}] {msg}")
        logger.info("[%s] %s", name, msg)

    return "\n".join(summary), 200


def _process_target(
    instances: compute_v1.InstancesClient,
    bucket: Any,
    name: str,
    zone: str,
    instance: str,
    probe: Callable[[str], bool | None],
) -> str:
    """Run one tick for one target. Returns the human-readable result."""
    state_blob = bucket.blob(f"state-{name}.json")

    vm = instances.get(project=PROJECT, zone=zone, instance=instance)
    if vm.status != "RUNNING":
        _write_state(state_blob, 0)
        return f"VM status is {vm.status}, no-op"

    public_ip = _public_ip(vm)
    if not public_ip:
        return "VM is RUNNING but has no public IP yet, no-op"

    is_active = probe(public_ip)
    if is_active is None:
        # Conservative: any probe failure does NOT count as empty.
        return f"probe to {public_ip} failed, no-op"

    state = _read_state(state_blob)
    if is_active:
        _write_state(state_blob, 0)
        return f"server is active, reset counter (was {state.get('consecutive_empty', 0)})"

    new_count = state.get("consecutive_empty", 0) + 1
    if new_count >= EMPTY_CHECKS_TO_STOP:
        instances.stop(project=PROJECT, zone=zone, instance=instance)
        _write_state(state_blob, 0)
        return f"stopped VM after {new_count} consecutive empty checks"

    _write_state(state_blob, new_count)
    return f"empty {new_count}/{EMPTY_CHECKS_TO_STOP}"


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
        logger.info("state read fallback (%s): %r", blob.name, exc)
        return {"consecutive_empty": 0}


def _write_state(blob: Any, consecutive_empty: int) -> None:
    payload = json.dumps({"consecutive_empty": consecutive_empty})
    blob.upload_from_string(payload, content_type="application/json")
