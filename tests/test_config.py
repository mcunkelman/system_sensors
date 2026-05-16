"""Tests for `system_sensors.config` YAML I/O and validation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from system_sensors.config import (
    ConfigError,
    SensorEntry,
    SensorsEnabled,
    Settings,
    load_sensors_enabled,
    load_settings,
    save_sensors_enabled,
    save_settings,
)


def _minimal_settings(**overrides: object) -> Settings:
    base = dict(
        mqtt_hostname="broker.local",
        mqtt_port=1883,
        client_id="system-sensors-host",
        timezone="UTC",
        device_name="host",
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_load_settings_missing_returns_none(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "settings.yaml") is None


def test_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    original = _minimal_settings(
        mqtt_username="u",
        mqtt_password="p",
        update_interval=30,
        tls_ca_certs="/etc/ssl/ca.pem",
        external_drives={"data": "/mnt/data", "backup": "/mnt/backup"},
    )
    save_settings(path, original)
    loaded = load_settings(path)
    assert loaded is not None
    assert loaded == original


def test_settings_missing_hostname_is_error(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(
        "mqtt:\n  port: 1883\nclient_id: x\ntimezone: UTC\ndevice_name: host\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_settings(path)


def test_settings_bad_timezone_is_error(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    save_settings(path, _minimal_settings())
    body = path.read_text(encoding="utf-8").replace("timezone: UTC", "timezone: Not/AZone")
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(path)


def test_settings_port_below_range(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    save_settings(path, _minimal_settings(mqtt_port=1))
    body = path.read_text(encoding="utf-8").replace("port: 1\n", "port: 0\n")
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(path)


def test_settings_port_above_range(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    save_settings(path, _minimal_settings(mqtt_port=8883))
    body = path.read_text(encoding="utf-8").replace("port: 8883", "port: 65536")
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(path)


def test_settings_port_ok_1883(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    save_settings(path, _minimal_settings(mqtt_port=1883))
    loaded = load_settings(path)
    assert loaded is not None
    assert loaded.mqtt_port == 1883


def test_device_name_lowercased_on_load(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    save_settings(path, _minimal_settings(device_name="MixedCaseHost"))
    loaded = load_settings(path)
    assert loaded is not None
    assert loaded.device_name == "mixedcasehost"


def test_save_settings_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    s = _minimal_settings()
    save_settings(path, s)
    first = path.read_bytes()
    save_settings(path, s)
    second = path.read_bytes()
    assert first == second


def test_sensors_enabled_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sensors_enabled.yaml"
    state = SensorsEnabled(
        sensors={
            "load_1m": SensorEntry(enabled=True, available=True),
            "power_status": SensorEntry(enabled=True, available=False),
        },
        last_probe_utc=datetime(2026, 5, 14, 17, 42, 0, tzinfo=timezone.utc),
    )
    save_sensors_enabled(path, state)
    loaded = load_sensors_enabled(path)
    assert loaded is not None
    assert loaded.sensors == state.sensors
    assert loaded.last_probe_utc == state.last_probe_utc


def test_load_sensors_enabled_missing_returns_none(tmp_path: Path) -> None:
    assert load_sensors_enabled(tmp_path / "sensors_enabled.yaml") is None


def test_sensors_enabled_file_is_sorted(tmp_path: Path) -> None:
    path = tmp_path / "sensors_enabled.yaml"
    state = SensorsEnabled(
        sensors={
            "z_last": SensorEntry(enabled=True, available=True),
            "a_first": SensorEntry(enabled=True, available=True),
            "m_mid": SensorEntry(enabled=False, available=True),
        },
        last_probe_utc=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    save_sensors_enabled(path, state)
    body = path.read_text(encoding="utf-8")
    a_idx = body.index("a_first")
    m_idx = body.index("m_mid")
    z_idx = body.index("z_last")
    assert a_idx < m_idx < z_idx
