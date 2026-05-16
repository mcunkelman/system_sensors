"""Tests for the Raspberry Pi sensors."""

from __future__ import annotations

import pytest

from system_sensors.sensors.base import SensorReading
from system_sensors.sensors.rpi import RpiPowerStatus, _is_raspberry_pi


def test_rpi_power_status_probe_returns_bool() -> None:
    """Probe must always return a bool — never crash on non-Pi hosts."""
    assert isinstance(RpiPowerStatus.probe(), bool)


def test_rpi_power_status_probe_false_on_non_pi() -> None:
    """The test host isn't a Pi; the probe must be False."""
    if _is_raspberry_pi():
        pytest.skip("running on a Raspberry Pi; probe behaviour differs")
    assert RpiPowerStatus.probe() is False


@pytest.mark.skipif(
    not RpiPowerStatus.probe(),
    reason="not a Raspberry Pi or rpi_bad_power not installed",
)
async def test_rpi_power_status_collect_returns_string_or_unavailable() -> None:
    reading = await RpiPowerStatus().collect()
    assert isinstance(reading, SensorReading)
    if reading.unavailable:
        assert reading.value is None
        return
    assert isinstance(reading.value, str)
    assert reading.value
