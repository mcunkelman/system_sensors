"""Intel GPU sensors via the `i915` / `xe` kernel drivers' sysfs hwmon interface.

Two per-GPU metrics, each backed by a single hwmon attribute file under
``/sys/class/drm/card<N>/device/hwmon/hwmon<M>/``:

- ``gpu_intel_temp``       -- core temperature, ``temp1_input``     (degrees C)
- ``gpu_intel_power_draw`` -- average package power, ``power1_average`` (W)

Driver match: DRM cards whose ``uevent`` reports ``DRIVER=i915`` (integrated
graphics, older discrete) or ``DRIVER=xe`` (newer driver for Arc and recent
discrete cards). Intel sysfs is sparser than AMD's — no junction temperature,
no VRAM temperature, no tachometer file. Cards lacking the specific hwmon
attribute for a metric are silently skipped at enumeration.

Resolved logical names follow ``gpu_intel_<card_index>_<metric>`` (e.g.
``gpu_intel_0_temp``, ``gpu_intel_0_power_draw``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from system_sensors.sensors._drm_hwmon import (
    enumerate_drm_cards_for_driver_with_file,
)
from system_sensors.sensors.base import Sensor, SensorReading

_INTEL_DRIVERS: tuple[str, ...] = ("i915", "xe")


class _IntelHwmonSensor(Sensor):
    """Shared probe / enumeration / naming for Intel hwmon-backed sensors.

    Intermediate base — intentionally has NO ``logical_name`` so the registry
    filters it. Concrete subclasses assign ``logical_name``, ``_HWMON_FILE``,
    and ``_METRIC_SUFFIX`` and override ``collect()``.
    """

    state_class: ClassVar[str | None] = "measurement"
    icon: ClassVar[str | None] = "mdi:expansion-card"
    priority: ClassVar[int] = 100

    # Filled in by concrete subclasses.
    _HWMON_FILE: ClassVar[str] = ""
    _METRIC_SUFFIX: ClassVar[str] = ""

    def __init__(self, card_index: int, hwmon_dir: Path) -> None:
        self._card_index = card_index
        self._hwmon_dir = hwmon_dir

    @classmethod
    def probe(cls) -> bool:
        return bool(
            enumerate_drm_cards_for_driver_with_file(_INTEL_DRIVERS, cls._HWMON_FILE)
        )

    @classmethod
    def enumerate_instances(cls) -> list[Sensor]:
        return [
            cls(index, hwmon)  # type: ignore[call-arg]
            for index, hwmon in enumerate_drm_cards_for_driver_with_file(
                _INTEL_DRIVERS, cls._HWMON_FILE
            )
        ]

    def resolved_logical_name(self) -> str:
        return f"gpu_intel_{self._card_index}_{type(self)._METRIC_SUFFIX}"

    async def collect(self) -> SensorReading:  # pragma: no cover - overridden
        raise NotImplementedError


class GpuIntelTemp(_IntelHwmonSensor):
    """Intel GPU temperature (degrees C) from ``temp1_input`` (milli-degC)."""

    logical_name: ClassVar[str] = "gpu_intel_temp"
    unit: ClassVar[str | None] = "°C"
    device_class: ClassVar[str | None] = "temperature"
    _HWMON_FILE: ClassVar[str] = "temp1_input"
    _METRIC_SUFFIX: ClassVar[str] = "temp"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            raw = (self._hwmon_dir / type(self)._HWMON_FILE).read_text(
                encoding="utf-8", errors="replace"
            )
            return SensorReading(value=int(raw.strip()) / 1000.0, timestamp=now)
        except (OSError, ValueError):
            return SensorReading(value=None, timestamp=now, unavailable=True)


class GpuIntelPowerDraw(_IntelHwmonSensor):
    """Intel GPU average package power (W) from ``power1_average`` (microwatts)."""

    logical_name: ClassVar[str] = "gpu_intel_power_draw"
    unit: ClassVar[str | None] = "W"
    device_class: ClassVar[str | None] = "power"
    _HWMON_FILE: ClassVar[str] = "power1_average"
    _METRIC_SUFFIX: ClassVar[str] = "power_draw"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            raw = (self._hwmon_dir / type(self)._HWMON_FILE).read_text(
                encoding="utf-8", errors="replace"
            )
            return SensorReading(
                value=int(raw.strip()) / 1_000_000.0, timestamp=now
            )
        except (OSError, ValueError):
            return SensorReading(value=None, timestamp=now, unavailable=True)
