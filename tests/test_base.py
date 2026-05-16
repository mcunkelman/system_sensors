"""Tests for the `Sensor` ABC contract."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import pytest

from system_sensors.sensors.base import Sensor, SensorReading


def test_sensor_is_abstract() -> None:
    """`Sensor` cannot be instantiated directly — `probe` and `collect` are abstract."""
    with pytest.raises(TypeError):
        Sensor()  # type: ignore[abstract]


def test_sensor_reading_is_frozen() -> None:
    """`SensorReading` is a frozen dataclass; mutation must fail."""
    reading = SensorReading(value=42, timestamp=datetime.now(timezone.utc))
    with pytest.raises(Exception):
        reading.value = 99  # type: ignore[misc]


def test_sensor_reading_defaults_unavailable_false() -> None:
    """`unavailable` defaults to False so the common-case construction is terse."""
    reading = SensorReading(value="ok", timestamp=datetime.now(timezone.utc))
    assert reading.unavailable is False


class _DummySensor(Sensor):
    """Minimal concrete sensor used to exercise the non-abstract defaults."""

    logical_name: ClassVar[str] = "dummy"

    @classmethod
    def probe(cls) -> bool:
        return True

    async def collect(self) -> SensorReading:
        return SensorReading(value=1, timestamp=datetime.now(timezone.utc))


def test_enumerate_instances_default_returns_single() -> None:
    """The default `enumerate_instances()` yields exactly one instance of `cls`."""
    instances = _DummySensor.enumerate_instances()
    assert len(instances) == 1
    assert isinstance(instances[0], _DummySensor)


def test_resolved_logical_name_default_returns_class_attribute() -> None:
    """The default `resolved_logical_name()` returns the class-level template."""
    inst = _DummySensor()
    assert inst.resolved_logical_name() == "dummy"
    assert inst.resolved_logical_name() == _DummySensor.logical_name
