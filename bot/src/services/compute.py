"""GCE compute control: start, stop, describe the Valheim VM.

Concrete GCP-only implementation. The cog calls these functions directly.

DESIGN NOTE -- single concrete impl vs abstract interface
=========================================================
We intentionally do NOT define a `ComputeProvider` ABC here. The bot only
talks to one cloud (GCP), and an unused abstraction is just code to read.

If we ever add a second provider (AWS EC2, Hetzner Cloud, etc.), the
refactor is mechanical:
  1. Rename this file to `gcp_compute.py`.
  2. Extract a `Protocol` or `ABC` with the three public functions below.
  3. Add `aws_ec2.py` with a parallel implementation.
  4. The cog imports the protocol and a factory rather than this module
     directly -- one-line edit per call site.

Threading note: google-cloud-compute ships a sync client. The public
functions here are `async def` and run the blocking calls under
`asyncio.to_thread` so callers never have to think about it.
"""

import asyncio
from dataclasses import dataclass
from functools import lru_cache

from google.cloud import compute_v1

from src.config.logging import get_logger

logger = get_logger(__name__)


WORLD_METADATA_KEY = "world-name"
WORLD_MODIFIERS_METADATA_KEY = "world-modifiers"


@dataclass(frozen=True)
class InstanceState:
    """A snapshot of VM state, suitable for embedding in a Discord response."""

    name: str
    zone: str
    status: str  # GCE statuses: PROVISIONING, STAGING, RUNNING, STOPPING, TERMINATED
    public_ip: str | None
    machine_type: str
    # The active world as recorded in the instance `world-name` metadata
    # key (what the startup-script reads into world.env on boot). This is
    # readable whether the VM is RUNNING or TERMINATED, so it's the
    # authoritative "which world will boot" signal. None when the key is
    # unset (fresh VM that still uses the Terraform default) -- callers
    # fall back to the status daemon / cache to name it.
    active_world: str | None = None
    # Raw SERVER_ARGS modifier string from the `world-modifiers` metadata
    # key (what the startup-script folds into SERVER_ARGS on boot). None
    # when unset (VM uses the Terraform default). Parsed by
    # src.services.modifiers for /valheim modifier list.
    server_modifiers_raw: str | None = None


@lru_cache(maxsize=1)
def _client() -> compute_v1.InstancesClient:
    return compute_v1.InstancesClient()


def _short_name(url: str) -> str:
    """GCE returns full URLs for zone/machineType — keep only the trailing segment."""
    return url.rsplit("/", 1)[-1] if url else url


def _public_ip(instance: compute_v1.Instance) -> str | None:
    for nic in instance.network_interfaces or []:
        for ac in nic.access_configs or []:
            if ac.nat_i_p:
                return ac.nat_i_p
    return None


def _metadata_value(instance: compute_v1.Instance, key: str) -> str | None:
    """Read a single instance-metadata value by key, or None if unset."""
    md = instance.metadata
    for item in (md.items or []) if md else []:
        if item.key == key:
            return item.value or None
    return None


async def describe_instance(project: str, zone: str, instance: str) -> InstanceState:
    """Return current VM state. Wraps the sync GCE call in a thread."""

    def _get() -> InstanceState:
        vm = _client().get(project=project, zone=zone, instance=instance)
        return InstanceState(
            name=vm.name,
            zone=_short_name(vm.zone),
            status=vm.status,
            public_ip=_public_ip(vm),
            machine_type=_short_name(vm.machine_type),
            active_world=_metadata_value(vm, WORLD_METADATA_KEY),
            server_modifiers_raw=_metadata_value(vm, WORLD_MODIFIERS_METADATA_KEY),
        )

    return await asyncio.to_thread(_get)


async def set_metadata(project: str, zone: str, instance: str, key: str, value: str) -> None:
    """Set a single instance-metadata key, preserving all other keys.

    A read-modify-write: GCE's setMetadata replaces the whole metadata
    block and requires the current fingerprint, so we fetch, swap just
    this key (leaving startup-script / ssh-keys / etc. untouched), and
    write back. The new value takes effect on the NEXT boot, when the
    startup-script reads it -- callers pair this with a stop/start.
    Idempotent: writing the current value is a harmless no-op.
    """

    def _set() -> None:
        client = _client()
        vm = client.get(project=project, zone=zone, instance=instance)
        md = vm.metadata
        items = [i for i in (md.items or []) if i.key != key]
        items.append(compute_v1.Items(key=key, value=value))
        client.set_metadata(
            project=project,
            zone=zone,
            instance=instance,
            metadata_resource=compute_v1.Metadata(fingerprint=md.fingerprint, items=items),
        )
        logger.info("set_metadata written", instance=instance, key=key, value=value)

    await asyncio.to_thread(_set)


async def set_world(project: str, zone: str, instance: str, world: str) -> None:
    """Set the active world via the `world-name` metadata key. Thin wrapper
    over set_metadata; see it for the read-modify-write semantics."""
    await set_metadata(project, zone, instance, WORLD_METADATA_KEY, world)


def _transition_sync(
    project: str,
    zone: str,
    instance: str,
    op_name: str,
    skip_if_status: str,
) -> bool:
    """Issue `op_name` (start/stop) on the VM. Returns True if the op was
    actually sent, False if the VM was already in the target state.

    Sync helper; the public functions wrap this in `asyncio.to_thread`.
    """
    vm = _client().get(project=project, zone=zone, instance=instance)
    if vm.status == skip_if_status:
        logger.info(f"{op_name}_instance noop: already {skip_if_status}", instance=instance)
        return False
    op_func = getattr(_client(), op_name)
    op_func(project=project, zone=zone, instance=instance)
    logger.info(f"{op_name}_instance issued", instance=instance, prior_status=vm.status)
    return True


async def start_instance(project: str, zone: str, instance: str) -> bool:
    """Start the VM. Idempotent. Returns True if a start was issued, False
    if already RUNNING. Returns once the op is enqueued; does not block
    until RUNNING.
    """
    return await asyncio.to_thread(
        _transition_sync, project, zone, instance, "start", "RUNNING"
    )


async def stop_instance(project: str, zone: str, instance: str) -> bool:
    """Stop the VM. Idempotent. Returns True if a stop was issued, False
    if already TERMINATED.
    """
    return await asyncio.to_thread(
        _transition_sync, project, zone, instance, "stop", "TERMINATED"
    )
