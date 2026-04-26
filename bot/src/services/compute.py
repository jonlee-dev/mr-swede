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

The public surface is deliberately narrow (three functions, plain dict
return for `describe_instance`) so that future-us can swap implementations
without touching the cog's display logic.
"""

from dataclasses import dataclass

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


def describe_instance() -> InstanceState:
    """Return current VM state.

    Phase 3: implement with google-cloud-compute. Reads project/zone/instance
    name from settings, calls instances().get(), maps response into InstanceState.
    """
    raise NotImplementedError("Phase 3: wire to google-cloud-compute Client.get()")


def start_instance() -> bool:
    """Start the VM. Returns True if the start operation was issued.

    Phase 3: idempotent -- if already RUNNING, return True without calling start.
    Otherwise calls instances().start() and returns once the operation is enqueued
    (does not block until RUNNING; the cog handles the wait UX separately).
    """
    raise NotImplementedError("Phase 3: wire to google-cloud-compute Client.start()")


def stop_instance() -> bool:
    """Stop the VM. Returns True if the stop operation was issued.

    Phase 3: idempotent -- if already TERMINATED, return True without calling stop.
    """
    raise NotImplementedError("Phase 3: wire to google-cloud-compute Client.stop()")
