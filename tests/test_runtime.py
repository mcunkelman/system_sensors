"""Tests for `system_sensors.runtime`."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest

from system_sensors.config import SensorEntry, SensorsEnabled, Settings
from system_sensors.runtime import (
    _build_active_sensors,
    _build_parser,
    _encode_value,
)
from system_sensors.sensors.base import Sensor, SensorReading
from system_sensors.sensors.disk import DiskUseMount


class _FakeSensor(Sensor):
    """Test-only sensor whose resolved name is set per instance."""

    logical_name = "fake"

    def __init__(self, name: str) -> None:
        self._name = name

    @classmethod
    def probe(cls) -> bool:
        return True

    def resolved_logical_name(self) -> str:
        return self._name

    async def collect(self) -> SensorReading:
        return SensorReading(value=1.0, timestamp=datetime.now(timezone.utc))


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = dict(
        mqtt_hostname="broker.local",
        mqtt_port=1883,
        client_id="system-sensors-host",
        timezone="UTC",
        device_name="host",
    )
    base.update(overrides)
    return Settings(**base)


def test_encode_value_bool_true() -> None:
    assert _encode_value(True) == "on"


def test_encode_value_bool_false() -> None:
    assert _encode_value(False) == "off"


def test_encode_value_int() -> None:
    assert _encode_value(42) == "42"


def test_encode_value_float_roundtrip() -> None:
    encoded = _encode_value(0.1 + 0.2)
    assert float(encoded) == 0.1 + 0.2


def test_encode_value_string_verbatim() -> None:
    assert _encode_value("hello") == "hello"


def test_parse_args_defaults() -> None:
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.log_level == "INFO"


def test_parse_args_accepts_log_level() -> None:
    parser = _build_parser()
    args = parser.parse_args(["--log-level", "DEBUG"])
    assert args.log_level == "DEBUG"


def test_parse_args_rejects_unknown_flag() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--no-such-flag"])


def test_build_active_sensors_filters_disabled_or_unavailable() -> None:
    sensors = [_FakeSensor("a"), _FakeSensor("b"), _FakeSensor("c")]
    state = SensorsEnabled(
        sensors={
            "a": SensorEntry(enabled=True, available=True),
            "b": SensorEntry(enabled=False, available=True),
            "c": SensorEntry(enabled=True, available=False),
        }
    )
    with (
        patch("system_sensors.runtime.discover_sensors", return_value=[]),
        patch("system_sensors.runtime.probe_all", return_value={}),
        patch("system_sensors.runtime.select_variants", return_value={}),
        patch(
            "system_sensors.runtime.instantiate_active_sensors", return_value=sensors
        ),
    ):
        active = _build_active_sensors(_settings(), state)
    names = [s.resolved_logical_name() for s in active]
    assert names == ["a"]


def test_build_active_sensors_skips_unknown_in_state() -> None:
    sensors = [_FakeSensor("a"), _FakeSensor("b")]
    state = SensorsEnabled(
        sensors={"a": SensorEntry(enabled=True, available=True)}
    )
    with (
        patch("system_sensors.runtime.discover_sensors", return_value=[]),
        patch("system_sensors.runtime.probe_all", return_value={}),
        patch("system_sensors.runtime.select_variants", return_value={}),
        patch(
            "system_sensors.runtime.instantiate_active_sensors", return_value=sensors
        ),
    ):
        active = _build_active_sensors(_settings(), state)
    assert [s.resolved_logical_name() for s in active] == ["a"]


def test_build_active_sensors_injects_external_drives() -> None:
    state = SensorsEnabled(
        sensors={
            "disk_use_backup": SensorEntry(enabled=True, available=True),
        }
    )
    settings = _settings(external_drives={"backup": "/mnt/backup"})
    with (
        patch("system_sensors.runtime.discover_sensors", return_value=[]),
        patch("system_sensors.runtime.probe_all", return_value={}),
        patch("system_sensors.runtime.select_variants", return_value={}),
        patch(
            "system_sensors.runtime.instantiate_active_sensors", return_value=[]
        ),
    ):
        active = _build_active_sensors(settings, state)
    assert len(active) == 1
    assert isinstance(active[0], DiskUseMount)
    assert active[0].resolved_logical_name() == "disk_use_backup"


def test_build_active_sensors_drops_unconfigured_external_drive() -> None:
    state = SensorsEnabled(sensors={})
    settings = _settings(external_drives={"backup": "/mnt/backup"})
    with (
        patch("system_sensors.runtime.discover_sensors", return_value=[]),
        patch("system_sensors.runtime.probe_all", return_value={}),
        patch("system_sensors.runtime.select_variants", return_value={}),
        patch(
            "system_sensors.runtime.instantiate_active_sensors", return_value=[]
        ),
    ):
        active = _build_active_sensors(settings, state)
    assert active == []
