"""CPU sensors.

Provides `CpuLoad1m`, `CpuLoad5m`, `CpuLoad15m`, `CpuUsage`, `ClockSpeed`,
plus thermal: `CpuTempMeanPsutil`, `CpuTempMeanSysfs`, `CpuTempMaxPsutil`,
`CpuTempMaxSysfs`, `CpuTempCorePsutil`, `CpuTempCoreSysfs`, and `FanSpeedPsutil`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar

import psutil

from system_sensors.sensors.base import Sensor, SensorReading

_log = logging.getLogger(__name__)

# Labels in `psutil.sensors_temperatures()` that represent CPU thermals.
# Order is preferred-first; first match wins.
_CPU_TEMP_LABELS: tuple[str, ...] = (
    "coretemp",
    "k10temp",
    "cpu_thermal",
    "cpu-thermal",
    "soc_thermal",
    "acpitz",
)

_THERMAL_ROOT = Path("/sys/class/thermal")


def _psutil_cpu_temps() -> list[float] | None:
    """Return per-core CPU temperatures from psutil, or None if unsupported.

    Iterates `_CPU_TEMP_LABELS` against the dict returned by psutil and uses
    the first matching non-empty group. Returns a list of floats (one per
    detected core/entry). Returns None when psutil has no `sensors_temperatures`
    attribute, returns an empty dict, or has no matching label.
    """
    if not hasattr(psutil, "sensors_temperatures"):
        return None
    try:
        raw = psutil.sensors_temperatures()
    except Exception:
        return None
    if not raw:
        return None
    for label in _CPU_TEMP_LABELS:
        entries = raw.get(label)
        if not entries:
            continue
        values: list[float] = []
        for entry in entries:
            current = getattr(entry, "current", None)
            if current is None:
                continue
            try:
                values.append(float(current))
            except (TypeError, ValueError):
                continue
        if values:
            return values
    return None


def _sysfs_cpu_temps() -> list[float] | None:
    """Return per-zone CPU temperatures from `/sys/class/thermal`, or None.

    Filters to thermal zones whose sibling `type` file mentions "cpu" (case-
    insensitive). Values are millidegrees Celsius in the kernel — divide by
    1000. Returns None if the root path doesn't exist or no CPU zones found.
    """
    if not _THERMAL_ROOT.exists():
        return None
    try:
        zones = sorted(_THERMAL_ROOT.glob("thermal_zone*"))
    except OSError:
        return None
    values: list[float] = []
    for zone in zones:
        type_path = zone / "type"
        temp_path = zone / "temp"
        if not type_path.exists() or not temp_path.exists():
            continue
        try:
            zone_type = type_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if "cpu" not in zone_type.lower():
            continue
        try:
            raw = temp_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        try:
            values.append(float(raw) / 1000.0)
        except ValueError:
            continue
    if not values:
        return None
    return values


class _CpuLoadBase(Sensor):
    """Shared probe + collect implementation for the three load horizons.

    `_LOAD_INDEX` selects which value to pull from `psutil.getloadavg()`:
    0 = 1-minute, 1 = 5-minute, 2 = 15-minute.

    This intermediate class is abstract (it does not set `logical_name` and
    `_LOAD_INDEX` is a sentinel) so the registry's `inspect.isabstract` filter
    leaves it out. Concrete subclasses set both class attributes.
    """

    unit: ClassVar[str | None] = None
    icon: ClassVar[str | None] = "mdi:cpu-64-bit"
    state_class: ClassVar[str | None] = "measurement"
    priority: ClassVar[int] = 100

    # Index into psutil.getloadavg(); overridden by each concrete subclass.
    _LOAD_INDEX: ClassVar[int] = -1

    @classmethod
    def probe(cls) -> bool:
        # psutil.getloadavg() is available on POSIX (Linux, macOS, *BSD).
        # On Windows, the attribute is absent. We check existence rather than
        # calling — probe must be cheap and side-effect free.
        return hasattr(psutil, "getloadavg")

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            values = psutil.getloadavg()
            value = round(float(values[type(self)._LOAD_INDEX]), 2)
            return SensorReading(value=value, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class CpuLoad1m(_CpuLoadBase):
    """1-minute load average."""

    logical_name: ClassVar[str] = "load_1m"
    _LOAD_INDEX: ClassVar[int] = 0


class CpuLoad5m(_CpuLoadBase):
    """5-minute load average."""

    logical_name: ClassVar[str] = "load_5m"
    _LOAD_INDEX: ClassVar[int] = 1


class CpuLoad15m(_CpuLoadBase):
    """15-minute load average."""

    logical_name: ClassVar[str] = "load_15m"
    _LOAD_INDEX: ClassVar[int] = 2


class CpuUsage(Sensor):
    """System-wide CPU utilization percentage via `psutil.cpu_percent`.

    psutil returns 0.0 on its first non-blocking call because there is no prior
    sample. The first instance call primes psutil's internal counter; if
    `collect()` runs within one second of `__init__`, the warmup value is
    returned and a warning is logged.
    """

    logical_name: ClassVar[str] = "cpu_usage"
    unit: ClassVar[str | None] = "%"
    state_class: ClassVar[str | None] = "measurement"
    icon: ClassVar[str | None] = "mdi:chip"
    priority: ClassVar[int] = 100

    _WARMUP_SECONDS: ClassVar[float] = 1.0

    def __init__(self) -> None:
        self._primed_at: datetime | None = None

    @classmethod
    def probe(cls) -> bool:
        return hasattr(psutil, "cpu_percent")

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            if self._primed_at is None:
                psutil.cpu_percent(interval=None)
                self._primed_at = now
                _log.warning(
                    "cpu_usage warmup: first sample primed, value will be 0 until next call"
                )
                return SensorReading(value=0.0, timestamp=now)
            if now - self._primed_at < timedelta(seconds=type(self)._WARMUP_SECONDS):
                _log.warning("cpu_usage collected within warmup window; value may be 0")
            value = float(psutil.cpu_percent(interval=None))
            return SensorReading(value=value, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class ClockSpeed(Sensor):
    """Current CPU clock frequency in MHz.

    `psutil.cpu_freq()` returns `None` on containers and some VMs; treat that
    as unavailable rather than raising.
    """

    logical_name: ClassVar[str] = "clock_speed"
    unit: ClassVar[str | None] = "MHz"
    device_class: ClassVar[str | None] = "frequency"
    state_class: ClassVar[str | None] = "measurement"
    icon: ClassVar[str | None] = "mdi:speedometer"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        return hasattr(psutil, "cpu_freq")

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            freq = psutil.cpu_freq()
            if freq is None:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            return SensorReading(value=int(freq.current), timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class _CpuTempBase(Sensor):
    """Shared HA-discovery attrs for thermal variants."""

    unit: ClassVar[str | None] = "°C"
    device_class: ClassVar[str | None] = "temperature"
    state_class: ClassVar[str | None] = "measurement"
    icon: ClassVar[str | None] = "mdi:thermometer"


class CpuTempMeanPsutil(_CpuTempBase):
    """Arithmetic mean of all per-core CPU temperatures via psutil."""

    logical_name: ClassVar[str] = "cpu_temp_mean"
    priority: ClassVar[int] = 200

    @classmethod
    def probe(cls) -> bool:
        return _psutil_cpu_temps() is not None

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            temps = _psutil_cpu_temps()
            if not temps:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            value = round(sum(temps) / len(temps), 1)
            return SensorReading(value=value, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class CpuTempMeanSysfs(_CpuTempBase):
    """Arithmetic mean of CPU thermal zones under `/sys/class/thermal`."""

    logical_name: ClassVar[str] = "cpu_temp_mean"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        return _sysfs_cpu_temps() is not None

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            temps = _sysfs_cpu_temps()
            if not temps:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            value = round(sum(temps) / len(temps), 1)
            return SensorReading(value=value, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class CpuTempMaxPsutil(_CpuTempBase):
    """Hottest per-core CPU temperature via psutil."""

    logical_name: ClassVar[str] = "cpu_temp_max"
    priority: ClassVar[int] = 200

    @classmethod
    def probe(cls) -> bool:
        return _psutil_cpu_temps() is not None

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            temps = _psutil_cpu_temps()
            if not temps:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            value = round(max(temps), 1)
            return SensorReading(value=value, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class CpuTempMaxSysfs(_CpuTempBase):
    """Hottest CPU thermal zone under `/sys/class/thermal`."""

    logical_name: ClassVar[str] = "cpu_temp_max"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        return _sysfs_cpu_temps() is not None

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            temps = _sysfs_cpu_temps()
            if not temps:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            value = round(max(temps), 1)
            return SensorReading(value=value, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class _CpuTempCoreMixin:
    """Per-core temperature instance state and naming."""

    def __init__(self, core_index: int) -> None:
        self._core_index = core_index

    def resolved_logical_name(self) -> str:
        return f"cpu_temp_core_{self._core_index}"


class CpuTempCorePsutil(_CpuTempCoreMixin, _CpuTempBase):
    """One instance per CPU core; values pulled from psutil at collect time."""

    logical_name: ClassVar[str] = "cpu_temp_core"
    priority: ClassVar[int] = 200

    @classmethod
    def probe(cls) -> bool:
        return _psutil_cpu_temps() is not None

    @classmethod
    def enumerate_instances(cls) -> list[Sensor]:
        temps = _psutil_cpu_temps()
        if not temps:
            return []
        return [cls(i) for i in range(len(temps))]

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            temps = _psutil_cpu_temps()
            if not temps or self._core_index >= len(temps):
                return SensorReading(value=None, timestamp=now, unavailable=True)
            return SensorReading(value=round(temps[self._core_index], 1), timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class CpuTempCoreSysfs(_CpuTempCoreMixin, _CpuTempBase):
    """One instance per CPU thermal zone; values pulled from sysfs."""

    logical_name: ClassVar[str] = "cpu_temp_core"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        return _sysfs_cpu_temps() is not None

    @classmethod
    def enumerate_instances(cls) -> list[Sensor]:
        temps = _sysfs_cpu_temps()
        if not temps:
            return []
        return [cls(i) for i in range(len(temps))]

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            temps = _sysfs_cpu_temps()
            if not temps or self._core_index >= len(temps):
                return SensorReading(value=None, timestamp=now, unavailable=True)
            return SensorReading(value=round(temps[self._core_index], 1), timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class FanSpeedPsutil(Sensor):
    """Maximum fan RPM across all psutil-reported fans.

    Upstream only inspected the `pwmfan` label (RPi-specific). We aggregate
    over every fan label psutil returns and take the max — matches the
    `cpu_temp_max` convention.
    """

    logical_name: ClassVar[str] = "fan_speed"
    unit: ClassVar[str | None] = "RPM"
    state_class: ClassVar[str | None] = "measurement"
    icon: ClassVar[str | None] = "mdi:fan"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        if not hasattr(psutil, "sensors_fans"):
            return False
        try:
            data = psutil.sensors_fans()
        except (NotImplementedError, Exception):
            return False
        return bool(data)

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            data = psutil.sensors_fans()
            if not data:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            readings: list[float] = []
            for entries in data.values():
                for entry in entries:
                    current = getattr(entry, "current", None)
                    if current is None:
                        continue
                    try:
                        readings.append(float(current))
                    except (TypeError, ValueError):
                        continue
            if not readings:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            return SensorReading(value=int(round(max(readings))), timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)
