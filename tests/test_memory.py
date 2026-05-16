"""Tests for memory sensors."""

from __future__ import annotations

from system_sensors.sensors.base import SensorReading
from system_sensors.sensors.memory import MemoryUse, SwapUsage


def test_memory_use_probe_true_on_host() -> None:
    assert MemoryUse.probe() is True


async def test_memory_use_collect_returns_float() -> None:
    reading = await MemoryUse().collect()
    assert isinstance(reading, SensorReading)
    assert reading.unavailable is False
    assert isinstance(reading.value, float)
    assert 0.0 <= reading.value <= 100.0


def test_swap_usage_probe_true_on_host() -> None:
    assert SwapUsage.probe() is True


async def test_swap_usage_collect_returns_float() -> None:
    reading = await SwapUsage().collect()
    assert isinstance(reading, SensorReading)
    assert reading.unavailable is False
    assert isinstance(reading.value, float)
    assert 0.0 <= reading.value <= 100.0
