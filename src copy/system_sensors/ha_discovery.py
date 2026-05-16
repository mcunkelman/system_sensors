"""Home Assistant MQTT-discovery payload assembly.

Pure functions, no I/O. Builds the HA discovery payload dict for one Sensor
instance, and the small set of topic strings used throughout the runtime.

Per port-surface.md, upstream built these via string concatenation. We use
typed dicts (json.dumps is called by the MQTT layer) and explicit topic
helpers so a single sensor name produces a stable triple of topics.
"""

from __future__ import annotations

from typing import Any

from system_sensors.config import Settings
from system_sensors.sensors.base import Sensor


def build_discovery_payload(sensor: Sensor, settings: Settings) -> dict[str, Any]:
    """Construct the HA MQTT-discovery payload for `sensor`.

    Topic shapes and field names follow the HA MQTT-discovery spec:
    https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
    """
    resolved = sensor.resolved_logical_name()
    device_name = settings.device_name
    payload: dict[str, Any] = {
        "name": resolved,
        "state_topic": state_topic(resolved, device_name),
        "availability_topic": availability_topic(device_name),
        "unique_id": f"system_sensors_{device_name}_{resolved}",
        "object_id": f"{device_name}_{resolved}",
        "device": {
            "identifiers": [f"system_sensors_{device_name}"],
            "name": device_name,
            "manufacturer": "system_sensors",
            "model": "agnostic-fork",
        },
    }
    cls = type(sensor)
    if cls.unit is not None:
        payload["unit_of_measurement"] = cls.unit
    if cls.device_class is not None:
        payload["device_class"] = cls.device_class
    if cls.state_class is not None:
        payload["state_class"] = cls.state_class
    if cls.icon is not None:
        payload["icon"] = cls.icon
    return payload


def state_topic(resolved_logical_name: str, device_name: str) -> str:
    """Per-sensor state topic. One topic per sensor (no last-iteration bug)."""
    return f"system-sensors/{device_name}/{resolved_logical_name}/state"


def availability_topic(device_name: str) -> str:
    """Single per-device availability topic used by the LWT and online ping."""
    return f"system-sensors/{device_name}/availability"


def discovery_topic(
    resolved_logical_name: str,
    device_name: str,
    sensor_type: str = "sensor",
) -> str:
    """HA-discovery config topic for a single sensor entity."""
    return f"homeassistant/{sensor_type}/{device_name}/{resolved_logical_name}/config"
