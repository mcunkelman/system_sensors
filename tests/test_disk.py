"""Tests for disk sensors."""

from __future__ import annotations

from system_sensors.sensors.base import SensorReading
from system_sensors.sensors.disk import DiskUseMount, DiskUseRoot


def test_disk_use_root_probe_true_on_host() -> None:
    assert DiskUseRoot.probe() is True


async def test_disk_use_root_collect_returns_float() -> None:
    reading = await DiskUseRoot().collect()
    assert isinstance(reading, SensorReading)
    assert reading.unavailable is False
    assert isinstance(reading.value, float)
    assert 0.0 <= reading.value <= 100.0


def test_disk_use_mount_enumerate_instances_is_empty() -> None:
    """Mount instances come from installer config, not auto-discovery."""
    assert DiskUseMount.enumerate_instances() == []


def test_disk_use_mount_resolved_name_uses_instance_value() -> None:
    """Each configured instance publishes under its own logical name."""
    instance = DiskUseMount("/", "disk_use_data")
    assert instance.resolved_logical_name() == "disk_use_data"


async def test_disk_use_mount_collect_returns_float() -> None:
    reading = await DiskUseMount("/", "disk_use_root_alias").collect()
    assert isinstance(reading, SensorReading)
    assert reading.unavailable is False
    assert isinstance(reading.value, float)
