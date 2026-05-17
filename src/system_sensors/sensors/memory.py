"""Memory sensors.

Provides `MemoryUse`, `SwapUsage`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import psutil

from system_sensors.sensors.base import Sensor, SensorReading


class MemoryUse(Sensor):
    """Virtual memory utilization percentage."""

    logical_name: ClassVar[str] = "memory_use"
    unit: ClassVar[str | None] = "%"
    state_class: ClassVar[str | None] = "measurement"
    icon: ClassVar[str | None] = "mdi:memory"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        return hasattr(psutil, "virtual_memory")

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            value = float(psutil.virtual_memory().percent)
            return SensorReading(value=value, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class SwapUsage(Sensor):
    """Swap memory utilization percentage."""

    logical_name: ClassVar[str] = "swap_usage"
    unit: ClassVar[str | None] = "%"
    state_class: ClassVar[str | None] = "measurement"
    icon: ClassVar[str | None] = "mdi:harddisk"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        return hasattr(psutil, "swap_memory")

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            value = float(psutil.swap_memory().percent)
            return SensorReading(value=value, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)
