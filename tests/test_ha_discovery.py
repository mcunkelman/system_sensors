"""Tests for `system_sensors.ha_discovery`."""

from __future__ import annotations

from system_sensors.config import Settings
from system_sensors.ha_discovery import (
    availability_topic,
    build_discovery_payload,
    discovery_topic,
    state_topic,
)
from system_sensors.sensors.cpu import CpuLoad1m


def _settings(device_name: str = "host") -> Settings:
    return Settings(
        mqtt_hostname="broker.local",
        mqtt_port=1883,
        client_id="system-sensors-host",
        timezone="UTC",
        device_name=device_name,
    )


def test_state_topic_shape() -> None:
    assert state_topic("load_1m", "host") == "system-sensors/host/load_1m/state"


def test_availability_topic_shape() -> None:
    assert availability_topic("host") == "system-sensors/host/availability"


def test_discovery_topic_default_type() -> None:
    assert (
        discovery_topic("load_1m", "host")
        == "homeassistant/sensor/host/load_1m/config"
    )


def test_discovery_topic_custom_type() -> None:
    assert (
        discovery_topic("foo", "host", sensor_type="binary_sensor")
        == "homeassistant/binary_sensor/host/foo/config"
    )


def test_build_discovery_payload_for_cpu_load_1m() -> None:
    payload = build_discovery_payload(CpuLoad1m(), _settings())
    assert payload["name"] == "load_1m"
    assert payload["state_topic"] == "system-sensors/host/load_1m/state"
    assert payload["availability_topic"] == "system-sensors/host/availability"
    assert payload["unique_id"] == "system_sensors_host_load_1m"
    assert payload["object_id"] == "host_load_1m"
    assert payload["icon"] == "mdi:cpu-64-bit"
    assert payload["state_class"] == "measurement"
    assert payload["device"]["identifiers"] == ["system_sensors_host"]
    assert payload["device"]["name"] == "host"
    assert payload["device"]["manufacturer"] == "system_sensors"
    assert payload["device"]["model"] == "agnostic-fork"


def test_build_discovery_payload_omits_none_class_attrs() -> None:
    payload = build_discovery_payload(CpuLoad1m(), _settings())
    assert "unit_of_measurement" not in payload
    assert "device_class" not in payload
