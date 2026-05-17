"""Sensor ABC and shared reading dataclass.

Platform-agnostic by design: nothing in this module may import `psutil`,
`paho.mqtt`, or any platform-specific tool. Sensor subclasses live in sibling
modules and may import whatever they need.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar


@dataclass(frozen=True)
class SensorReading:
    """One value emitted by a `Sensor.collect()` call.

    Attributes:
        value: Sensor value. Typed as `Any` because sensors can emit
            `str | int | float | bool` depending on `sensor_type`.
        timestamp: When the reading was taken (caller-supplied UTC datetime).
        unavailable: True if `collect()` failed and the value should be ignored.
            The runtime publishes the `availability` topic accordingly rather
            than crashing the loop.
    """

    value: Any
    timestamp: datetime
    unavailable: bool = False


class Sensor(ABC):
    """Base class for all sensor variants.

    One subclass per platform/tool variant of a logical sensor. For example, the
    logical sensor `wifi_strength` may have variants `WifiStrengthIw`,
    `WifiStrengthNmcli`, `WifiStrengthProc`. They share `logical_name` but differ
    in `probe()` checks and `priority`.

    Class attributes describe the sensor for HA discovery and registry selection.
    Per-instance state (e.g. previous counter values for delta computation) lives
    on the instance, set up in `__init__` or lazily inside `collect()`.

    Multi-instance hardware (e.g. multiple GPUs) is modelled by overriding
    `enumerate_instances()` to return one `Sensor` per physical device, each with
    a distinct `resolved_logical_name()`. Single-instance sensors get the default
    behaviour for free: a single instance whose resolved name is the class
    attribute.
    """

    # Logical identity — variants of the same sensor share this. For
    # multi-instance sensors this is a TEMPLATE; `resolved_logical_name()` returns
    # the per-instance name that is actually published.
    logical_name: ClassVar[str]

    # MQTT / HA discovery shape.
    sensor_type: ClassVar[str] = "sensor"
    unit: ClassVar[str | None] = None
    device_class: ClassVar[str | None] = None
    state_class: ClassVar[str | None] = None
    icon: ClassVar[str | None] = None

    # Higher wins when multiple variants probe True for the same `logical_name`.
    priority: ClassVar[int] = 0

    @classmethod
    @abstractmethod
    def probe(cls) -> bool:
        """Return True if this variant can run on the current host.

        Must be cheap and side-effect free: typical implementations check for
        a binary on PATH (`shutil.which`), the existence of a sysfs path, or a
        successful import. Called once at install time, never at runtime.
        """
        ...

    @abstractmethod
    async def collect(self) -> SensorReading:
        """Read the current value.

        Async because some sensors shell out or are slow (e.g. `apt` probes).
        Implementations should NOT raise — wrap failures and return a
        `SensorReading(value=..., unavailable=True, timestamp=...)` instead.
        The runtime catches stray exceptions defensively, but raising is a bug.
        """
        ...

    @classmethod
    def enumerate_instances(cls) -> list["Sensor"]:
        """Return one `Sensor` instance per physical device this class represents.

        Default: a single instance (`[cls()]`). Multi-instance sensors (e.g. GPU
        temperature, one entity per GPU) override this to enumerate the hardware
        (NVML device count, `/sys/class/drm/card*`, etc.) and return one instance
        per device, each with a distinct `resolved_logical_name()`.

        Called by the registry AFTER `probe()` returns True. Implementations may
        assume the host has at least one device of this type.
        """
        return [cls()]

    def resolved_logical_name(self) -> str:
        """Per-instance logical name as published to MQTT / HA.

        Default returns the class attribute verbatim — correct for single-
        instance sensors. Multi-instance sensors override this so each instance
        has a unique name (e.g. `gpu_nvidia_0_temp`, `gpu_nvidia_1_temp`).
        """
        return type(self).logical_name

    # Discovery payload assembly lives in system_sensors.ha_discovery.
    # Call build_discovery_payload(sensor, settings) from that module;
    # do not call this method directly.
