"""Unit tests for src.services.compute."""

from unittest.mock import MagicMock, patch

from src.services.compute import (
    InstanceState,
    describe_instance,
    start_instance,
    stop_instance,
)


def _fake_instance(
    name: str = "valheim-server",
    status: str = "RUNNING",
    public_ip: str | None = "1.2.3.4",
    machine_type_url: str = "https://www.googleapis.com/compute/v1/projects/p/zones/us-central1-a/machineTypes/e2-standard-2",
    zone_url: str = "https://www.googleapis.com/compute/v1/projects/p/zones/us-central1-a",
):
    """Build a MagicMock that mimics the google.cloud.compute_v1.Instance shape."""
    instance = MagicMock()
    instance.name = name
    instance.status = status
    instance.zone = zone_url
    instance.machine_type = machine_type_url
    if public_ip is None:
        instance.network_interfaces = []
    else:
        access_config = MagicMock()
        access_config.nat_i_p = public_ip
        nic = MagicMock()
        nic.access_configs = [access_config]
        instance.network_interfaces = [nic]
    return instance


class TestDescribeInstance:
    async def test_running_with_public_ip(self):
        client = MagicMock()
        client.get.return_value = _fake_instance(status="RUNNING", public_ip="1.2.3.4")
        with patch("src.services.compute._client", return_value=client):
            state = await describe_instance("p", "us-central1-a", "valheim-server")
        assert state == InstanceState(
            name="valheim-server",
            zone="us-central1-a",
            status="RUNNING",
            public_ip="1.2.3.4",
            machine_type="e2-standard-2",
        )

    async def test_terminated_has_no_public_ip(self):
        client = MagicMock()
        client.get.return_value = _fake_instance(status="TERMINATED", public_ip=None)
        with patch("src.services.compute._client", return_value=client):
            state = await describe_instance("p", "us-central1-a", "valheim-server")
        assert state.status == "TERMINATED"
        assert state.public_ip is None

    async def test_forwards_args_to_client(self):
        client = MagicMock()
        client.get.return_value = _fake_instance()
        with patch("src.services.compute._client", return_value=client):
            await describe_instance("my-proj", "us-east1-b", "vh")
        client.get.assert_called_once_with(project="my-proj", zone="us-east1-b", instance="vh")


class TestStartInstance:
    async def test_already_running_returns_false(self):
        client = MagicMock()
        client.get.return_value = _fake_instance(status="RUNNING")
        with patch("src.services.compute._client", return_value=client):
            issued = await start_instance("p", "z", "i")
        assert issued is False
        client.start.assert_not_called()

    async def test_starts_when_terminated(self):
        client = MagicMock()
        client.get.return_value = _fake_instance(status="TERMINATED")
        with patch("src.services.compute._client", return_value=client):
            issued = await start_instance("p", "z", "i")
        assert issued is True
        client.start.assert_called_once_with(project="p", zone="z", instance="i")


class TestStopInstance:
    async def test_already_terminated_returns_false(self):
        client = MagicMock()
        client.get.return_value = _fake_instance(status="TERMINATED")
        with patch("src.services.compute._client", return_value=client):
            issued = await stop_instance("p", "z", "i")
        assert issued is False
        client.stop.assert_not_called()

    async def test_stops_when_running(self):
        client = MagicMock()
        client.get.return_value = _fake_instance(status="RUNNING")
        with patch("src.services.compute._client", return_value=client):
            issued = await stop_instance("p", "z", "i")
        assert issued is True
        client.stop.assert_called_once_with(project="p", zone="z", instance="i")
