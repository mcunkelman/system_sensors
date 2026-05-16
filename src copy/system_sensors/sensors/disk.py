"""Disk sensors.

Provides `DiskUseRoot` (auto-instantiated for `/`) and `DiskUseMount`
(configured per external mount by the installer).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import psutil

from system_sensors.sensors.base import Sensor, SensorReading


class DiskUseRoot(Sensor):
    """Filesystem usage percentage for the root mount."""

    logical_name: ClassVar[str] = "disk_use"
    unit: ClassVar[str | None] = "%"
    state_class: ClassVar[str | None] = "measurement"
    icon: ClassVar[str | None] = "mdi:harddisk"
    priority: ClassVar[int] = 100

    _PATH: ClassVar[str] = "/"

    @classmethod
    def probe(cls) -> bool:
        return hasattr(psutil, "disk_usage")

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            value = float(psutil.disk_usage(type(self)._PATH).percent)
            return SensorReading(value=value, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class DiskUseMount(Sensor):
    """Filesystem usage percentage for an installer-configured mount point.

    Multi-instance, configured: each instance carries its own `mount_point` and
    `logical_name`. `enumerate_instances()` returns an empty list because the
    installer injects pre-constructed instances based on the `external_drives`
    config — discovery does not enumerate mounts on its own.
    """

    logical_name: ClassVar[str] = "disk_use_mount"
    unit: ClassVar[str | None] = "%"
    state_class: ClassVar[str | None] = "measurement"
    icon: ClassVar[str | None] = "mdi:harddisk"
    priority: ClassVar[int] = 100

    def __init__(self, mount_point: str, logical_name: str) -> None:
        self._mount_point = mount_point
        self._instance_logical_name = logical_name

    @classmethod
    def probe(cls) -> bool:
        return True

    @classmethod
    def enumerate_instances(cls) -> list[Sensor]:
        # TODO(installer): inject DiskUseMount instances from external_drives config.
        return []

    def resolved_logical_name(self) -> str:
        return self._instance_logical_name

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            value = float(psutil.disk_usage(self._mount_point).percent)
            return SensorReading(value=value, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)
