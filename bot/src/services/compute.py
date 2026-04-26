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


@dataclass(frozen=True)
class InstanceState:
    """A snapshot of VM state, suitable for embedding in a Discord response."""

    name: str
    zone: str
    status: str  # GCE statuses: PROVISIONING, STAGING, RUNNING, STOPPING, TERMINATED
    public_ip: str | None
    machine_type: str


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
        )

    return await asyncio.to_thread(_get)


async def start_instance(project: str, zone: str, instance: str) -> bool:
    """Start the VM. Idempotent. Returns True if a start was issued, False if already RUNNING.

    Returns once the operation is enqueued. Does not block until RUNNING.
    """

    def _start() -> bool:
        vm = _client().get(project=project, zone=zone, instance=instance)
        if vm.status == "RUNNING":
            logger.info("start_instance noop: already running", instance=instance)
            return False
        _client().start(project=project, zone=zone, instance=instance)
        logger.info("start_instance issued", instance=instance, prior_status=vm.status)
        return True

    return await asyncio.to_thread(_start)


async def stop_instance(project: str, zone: str, instance: str) -> bool:
    """Stop the VM. Idempotent. Returns True if a stop was issued, False if already TERMINATED."""

    def _stop() -> bool:
        vm = _client().get(project=project, zone=zone, instance=instance)
        if vm.status == "TERMINATED":
            logger.info("stop_instance noop: already terminated", instance=instance)
            return False
        _client().stop(project=project, zone=zone, instance=instance)
        logger.info("stop_instance issued", instance=instance, prior_status=vm.status)
        return True

    return await asyncio.to_thread(_stop)
