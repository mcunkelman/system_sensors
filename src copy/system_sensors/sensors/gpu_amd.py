"""AMD GPU sensors via the `amdgpu` kernel driver's sysfs hwmon interface.

Five per-GPU metrics, each backed by a single hwmon attribute file under
``/sys/class/drm/card<N>/device/hwmon/hwmon<M>/``:

- ``gpu_amd_temp``          -- core/edge temperature, ``temp1_input``      (degrees C)
- ``gpu_amd_temp_junction`` -- junction (hotspot) temperature, ``temp2_input`` (degrees C)
- ``gpu_amd_temp_memory``   -- VRAM/HBM temperature, ``temp3_input``       (degrees C)
- ``gpu_amd_fan_speed``     -- fan tachometer, ``fan1_input``              (RPM)
- ``gpu_amd_power_draw``    -- average board power, ``power1_average``     (W)

Driver match: only DRM cards whose ``uevent`` reports ``DRIVER=amdgpu`` are
enumerated. Cards without the specific hwmon file for a given metric are
silently skipped — no rocm-smi or other subprocess fallback in v1.

Resolved logical names follow ``gpu_amd_<card_index>_<metric>`` (e.g.
``gpu_amd_0_temp``, ``gpu_amd_1_fan_speed``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from system_sensors.sensors._drm_hwmon import (
    enumerate_drm_cards_for_driver_with_file,
)
from system_sensors.sensors.base import Sensor, SensorReading

_AMD_DRIVERS: tuple[str, ...] = ("amdgpu",)


class _AmdHwmonSensor(Sensor):
    """Shared probe / enumeration / naming for amdgpu hwmon-backed sensors.

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
            enumerate_drm_cards_for_driver_with_file(_AMD_DRIVERS, cls._HWMON_FILE)
        )

    @classmethod
    def enumerate_instances(cls) -> list[Sensor]:
        return [
            cls(index, hwmon)  # type: ignore[call-arg]
            for index, hwmon in enumerate_drm_cards_for_driver_with_file(
                _AMD_DRIVERS, cls._HWMON_FILE
            )
        ]

    def resolved_logical_name(self) -> str:
        return f"gpu_amd_{self._card_index}_{type(self)._METRIC_SUFFIX}"

    async def collect(self) -> SensorReading:  # pragma: no cover - overridden
        raise NotImplementedError


class GpuAmdTemp(_AmdHwmonSensor):
    """AMD GPU core/edge temperature (degrees C) from ``temp1_input`` (milli-degC)."""

    logical_name: ClassVar[str] = "gpu_amd_temp"
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


class GpuAmdTempJunction(_AmdHwmonSensor):
    """AMD GPU junction (hotspot) temperature (degrees C) from ``temp2_input``."""

    logical_name: ClassVar[str] = "gpu_amd_temp_junction"
    unit: ClassVar[str | None] = "°C"
    device_class: ClassVar[str | None] = "temperature"
    _HWMON_FILE: ClassVar[str] = "temp2_input"
    _METRIC_SUFFIX: ClassVar[str] = "temp_junction"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            raw = (self._hwmon_dir / type(self)._HWMON_FILE).read_text(
                encoding="utf-8", errors="replace"
            )
            return SensorReading(value=int(raw.strip()) / 1000.0, timestamp=now)
        except (OSError, ValueError):
            return SensorReading(value=None, timestamp=now, unavailable=True)


class GpuAmdTempMemory(_AmdHwmonSensor):
    """AMD GPU memory/HBM temperature (degrees C) from ``temp3_input``."""

    logical_name: ClassVar[str] = "gpu_amd_temp_memory"
    unit: ClassVar[str | None] = "°C"
    device_class: ClassVar[str | None] = "temperature"
    _HWMON_FILE: ClassVar[str] = "temp3_input"
    _METRIC_SUFFIX: ClassVar[str] = "temp_memory"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            raw = (self._hwmon_dir / type(self)._HWMON_FILE).read_text(
                encoding="utf-8", errors="replace"
            )
            return SensorReading(value=int(raw.strip()) / 1000.0, timestamp=now)
        except (OSError, ValueError):
            return SensorReading(value=None, timestamp=now, unavailable=True)


class GpuAmdFanSpeed(_AmdHwmonSensor):
    """AMD GPU fan speed (RPM) from ``fan1_input``.

    Tachometer raw value — no conversion. Cards without a controllable fan
    omit the file entirely and are filtered out at enumeration time.
    """

    logical_name: ClassVar[str] = "gpu_amd_fan_speed"
    unit: ClassVar[str | None] = "RPM"
    device_class: ClassVar[str | None] = None
    _HWMON_FILE: ClassVar[str] = "fan1_input"
    _METRIC_SUFFIX: ClassVar[str] = "fan_speed"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            raw = (self._hwmon_dir / type(self)._HWMON_FILE).read_text(
                encoding="utf-8", errors="replace"
            )
            return SensorReading(value=int(raw.strip()), timestamp=now)
        except (OSError, ValueError):
            return SensorReading(value=None, timestamp=now, unavailable=True)


class GpuAmdPowerDraw(_AmdHwmonSensor):
    """AMD GPU average board power (W) from ``power1_average`` (microwatts)."""

    logical_name: ClassVar[str] = "gpu_amd_power_draw"
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
