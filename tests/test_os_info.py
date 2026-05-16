"""Tests for OS metadata sensors."""

from __future__ import annotations

from datetime import datetime

from pathlib import Path

import pytest

from system_sensors.sensors.base import SensorReading
from system_sensors.sensors.os_info import HostArch, HostIp, HostOs, Hostname, LastBoot


def test_hostname_probe_true() -> None:
    assert Hostname.probe() is True


async def test_hostname_collect_returns_string() -> None:
    reading = await Hostname().collect()
    assert isinstance(reading, SensorReading)
    assert reading.unavailable is False
    assert isinstance(reading.value, str)
    assert reading.value


def test_host_ip_probe_true_on_host() -> None:
    assert HostIp.probe() is True


async def test_host_ip_collect_returns_non_loopback_ipv4() -> None:
    reading = await HostIp().collect()
    assert isinstance(reading, SensorReading)
    # On hosts with only loopback (rare), unavailable is acceptable.
    if reading.unavailable:
        assert reading.value is None
        return
    assert isinstance(reading.value, str)
    assert not reading.value.startswith("127.")
    assert not reading.value.startswith("169.254.")


def test_host_arch_probe_true() -> None:
    assert HostArch.probe() is True


async def test_host_arch_collect_returns_string() -> None:
    reading = await HostArch().collect()
    assert reading.unavailable is False
    assert isinstance(reading.value, str)
    assert reading.value


def test_last_boot_probe_true_on_host() -> None:
    assert LastBoot.probe() is True


async def test_last_boot_collect_returns_iso_string() -> None:
    reading = await LastBoot().collect()
    assert reading.unavailable is False
    assert isinstance(reading.value, str)
    parsed = datetime.fromisoformat(reading.value)
    assert parsed.tzinfo is not None


def test_host_os_probe_matches_etc_os_release_presence() -> None:
    assert HostOs.probe() is Path("/etc/os-release").exists()


@pytest.mark.skipif(not Path("/etc/os-release").exists(), reason="no /etc/os-release on this host")
async def test_host_os_collect_returns_string_when_probe_true() -> None:
    reading = await HostOs().collect()
    assert isinstance(reading, SensorReading)
    assert reading.unavailable is False
    assert isinstance(reading.value, str)
    assert reading.value
