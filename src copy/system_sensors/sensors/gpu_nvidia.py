"""NVIDIA GPU sensors.

Six per-GPU metrics, each with two probe variants:

- `gpu_nvidia_temp`        — GPU core temperature (degrees C)
- `gpu_nvidia_utilization` — GPU compute utilization (%)
- `gpu_nvidia_memory_used` — VRAM in use (MB)
- `gpu_nvidia_memory_total`— VRAM capacity (MB)
- `gpu_nvidia_power_draw`  — Instantaneous board power draw (W)
- `gpu_nvidia_fan_speed`   — Fan speed as a percentage of max (%)

Variant strategy (per metric):
- NVML (`pynvml` / `nvidia-ml-py`) — `priority=200`. Preferred: in-process,
  no subprocess overhead, structured types.
- `nvidia-smi` CLI — `priority=100`. Universal fallback: every NVIDIA driver
  install ships `nvidia-smi`, no extra pip dependency required.

On a host with NVIDIA GPUs and both backends installed, both variants probe
True for each metric. The registry keeps only the higher-priority NVML variant
per logical name, so we never double-publish a GPU's metric.

Enumeration is lazy: NVML init and `nvidia-smi --list-gpus` execution happen
inside `enumerate_instances()` (called at install time), never at module
import time. Probe-time checks are limited to a cached NVML count and a
cheap subprocess-list invocation.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, ClassVar

from system_sensors.sensors.base import Sensor, SensorReading

_log = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT_SECONDS: float = 25.0
_PROBE_LIST_TIMEOUT_SECONDS: float = 5.0

# NVML init cache. Three states:
#   None  — not yet attempted
#   False — attempted and failed; do not retry
#   module — pynvml module after a successful nvmlInit()
_NVML_STATE: Any = None


def _try_init_nvml() -> Any:
    """Lazily attempt `pynvml.nvmlInit()`.

    Caches the result so subsequent callers either get the live module or
    short-circuit. `pynvml.nvmlShutdown()` is intentionally never called — the
    process holds the handle for its lifetime and the OS cleans up on exit.

    Returns:
        The imported `pynvml` module on success, or `None` if the import or
        `nvmlInit()` call failed.
    """
    global _NVML_STATE
    if _NVML_STATE is False:
        return None
    if _NVML_STATE is not None:
        return _NVML_STATE
    try:
        # `nvidia-ml-py` (the modern PyPI name) and the older `pynvml`
        # both expose the same module name `pynvml`.
        import pynvml  # type: ignore[import-not-found]
    except Exception:
        _NVML_STATE = False
        return None
    try:
        pynvml.nvmlInit()
    except Exception:
        _NVML_STATE = False
        return None
    _NVML_STATE = pynvml
    return pynvml


def _nvml_device_count() -> int:
    """Number of NVIDIA GPUs visible via NVML. 0 when NVML is unavailable."""
    pynvml = _try_init_nvml()
    if pynvml is None:
        return 0
    try:
        return int(pynvml.nvmlDeviceGetCount())
    except Exception:
        return 0


def _nvidia_smi_device_count() -> int:
    """Number of GPUs reported by `nvidia-smi --list-gpus`.

    Synchronous, cheap, intentionally not cached (called only at install
    time from `enumerate_instances`). Returns 0 on any failure.
    """
    if shutil.which("nvidia-smi") is None:
        return 0
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--list-gpus"],
            capture_output=True,
            timeout=_PROBE_LIST_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception:
        return 0
    if completed.returncode != 0:
        return 0
    text = completed.stdout.decode("utf-8", errors="replace")
    return sum(1 for line in text.splitlines() if line.strip())


async def _nvidia_smi_query(field: str, gpu_index: int) -> str | None:
    """Run `nvidia-smi --id=<n> --query-gpu=<field> --format=csv,noheader,nounits`.

    Returns the stripped stdout string on success, or `None` on timeout,
    non-zero exit, or any subprocess error. `[N/A]` and empty stdout are
    treated as `None` by callers, not here.
    """
    if shutil.which("nvidia-smi") is None:
        return None
    argv = [
        "nvidia-smi",
        f"--id={gpu_index}",
        f"--query-gpu={field}",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return None
    except Exception:
        return None
    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_SUBPROCESS_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return None
    if proc.returncode != 0:
        return None
    return (stdout or b"").decode("utf-8", errors="replace").strip()


def _is_na(value: str | None) -> bool:
    """True if `value` is missing or an nvidia-smi not-available sentinel.

    nvidia-smi uses several sentinel strings for unsupported or unavailable
    metrics: ``[N/A]``, ``[Not Supported]``, ``[Unknown Error]``, etc.
    All start with ``[N`` or ``[U`` after stripping whitespace. We match the
    opening bracket plus first letter rather than enumerating every variant.
    """
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    upper = stripped.upper()
    # All known nvidia-smi sentinels start with [ and contain N or U after it
    if upper.startswith("[N") or upper.startswith("[U"):
        return True
    return False


# ---------------------------------------------------------------------------
# NVML variant base
# ---------------------------------------------------------------------------


class _NvmlGpuMixin:
    """Per-instance state + enumeration for NVML-backed GPU sensors.

    Concrete subclasses define `_metric_suffix` and override `collect()` to
    issue the appropriate NVML call against `self._gpu_index`.
    """

    _metric_suffix: ClassVar[str] = ""

    def __init__(self, gpu_index: int) -> None:
        self._gpu_index = gpu_index

    @classmethod
    def enumerate_instances(cls) -> list[Sensor]:
        count = _nvml_device_count()
        return [cls(i) for i in range(count)]  # type: ignore[call-arg]

    def resolved_logical_name(self) -> str:
        return f"gpu_nvidia_{self._gpu_index}_{type(self)._metric_suffix}"


class _NvmlVariantSensor(_NvmlGpuMixin, Sensor):
    """Intermediate base — intentionally has NO `logical_name` so the registry
    filters it out. Concrete variants below assign `logical_name` themselves."""

    priority: ClassVar[int] = 200
    icon: ClassVar[str | None] = "mdi:expansion-card"
    state_class: ClassVar[str | None] = "measurement"

    @classmethod
    def probe(cls) -> bool:
        return _nvml_device_count() > 0

    async def collect(self) -> SensorReading:  # pragma: no cover - overridden
        raise NotImplementedError


class GpuNvidiaTempNvml(_NvmlVariantSensor):
    """GPU temperature via `nvmlDeviceGetTemperature`."""

    logical_name: ClassVar[str] = "gpu_nvidia_temp"
    unit: ClassVar[str | None] = "°C"
    device_class: ClassVar[str | None] = "temperature"
    _metric_suffix: ClassVar[str] = "temp"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        pynvml = _try_init_nvml()
        if pynvml is None:
            return SensorReading(value=None, timestamp=now, unavailable=True)
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(self._gpu_index)
            value = int(
                pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            )
            return SensorReading(value=value, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class GpuNvidiaUtilizationNvml(_NvmlVariantSensor):
    """GPU compute utilization via `nvmlDeviceGetUtilizationRates`."""

    logical_name: ClassVar[str] = "gpu_nvidia_utilization"
    unit: ClassVar[str | None] = "%"
    _metric_suffix: ClassVar[str] = "utilization"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        pynvml = _try_init_nvml()
        if pynvml is None:
            return SensorReading(value=None, timestamp=now, unavailable=True)
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(self._gpu_index)
            rates = pynvml.nvmlDeviceGetUtilizationRates(handle)
            return SensorReading(value=int(rates.gpu), timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class GpuNvidiaMemoryUsedNvml(_NvmlVariantSensor):
    """VRAM in use via `nvmlDeviceGetMemoryInfo` (`.used`, bytes -> MB)."""

    logical_name: ClassVar[str] = "gpu_nvidia_memory_used"
    unit: ClassVar[str | None] = "MB"
    _metric_suffix: ClassVar[str] = "memory_used"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        pynvml = _try_init_nvml()
        if pynvml is None:
            return SensorReading(value=None, timestamp=now, unavailable=True)
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(self._gpu_index)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            return SensorReading(value=int(info.used // (1024 * 1024)), timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class GpuNvidiaMemoryTotalNvml(_NvmlVariantSensor):
    """VRAM capacity via `nvmlDeviceGetMemoryInfo` (`.total`, bytes -> MB).

    Capacity does not change at runtime, so this metric uses
    `state_class=None` (deviating from the per-class default at the
    intermediate base).
    """

    logical_name: ClassVar[str] = "gpu_nvidia_memory_total"
    unit: ClassVar[str | None] = "MB"
    state_class: ClassVar[str | None] = None
    _metric_suffix: ClassVar[str] = "memory_total"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        pynvml = _try_init_nvml()
        if pynvml is None:
            return SensorReading(value=None, timestamp=now, unavailable=True)
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(self._gpu_index)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            return SensorReading(value=int(info.total // (1024 * 1024)), timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class GpuNvidiaPowerDrawNvml(_NvmlVariantSensor):
    """Instantaneous power draw via `nvmlDeviceGetPowerUsage` (mW -> W)."""

    logical_name: ClassVar[str] = "gpu_nvidia_power_draw"
    unit: ClassVar[str | None] = "W"
    device_class: ClassVar[str | None] = "power"
    _metric_suffix: ClassVar[str] = "power_draw"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        pynvml = _try_init_nvml()
        if pynvml is None:
            return SensorReading(value=None, timestamp=now, unavailable=True)
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(self._gpu_index)
            milliwatts = pynvml.nvmlDeviceGetPowerUsage(handle)
            return SensorReading(value=float(milliwatts) / 1000.0, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class GpuNvidiaFanSpeedNvml(_NvmlVariantSensor):
    """Fan speed as a percentage of max via `nvmlDeviceGetFanSpeed`.

    Datacenter cards and laptops without a controllable fan raise
    `NVMLError_NotSupported` — caught here and reported as `unavailable`.
    """

    logical_name: ClassVar[str] = "gpu_nvidia_fan_speed"
    unit: ClassVar[str | None] = "%"
    _metric_suffix: ClassVar[str] = "fan_speed"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        pynvml = _try_init_nvml()
        if pynvml is None:
            return SensorReading(value=None, timestamp=now, unavailable=True)
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(self._gpu_index)
            return SensorReading(
                value=int(pynvml.nvmlDeviceGetFanSpeed(handle)), timestamp=now
            )
        except Exception:
            # Includes `NVMLError_NotSupported` on cards without a controllable fan.
            return SensorReading(value=None, timestamp=now, unavailable=True)


# ---------------------------------------------------------------------------
# nvidia-smi variant base
# ---------------------------------------------------------------------------


class _NvidiaSmiGpuMixin:
    """Per-instance state + enumeration for `nvidia-smi`-backed GPU sensors."""

    _metric_suffix: ClassVar[str] = ""

    def __init__(self, gpu_index: int) -> None:
        self._gpu_index = gpu_index

    @classmethod
    def enumerate_instances(cls) -> list[Sensor]:
        count = _nvidia_smi_device_count()
        return [cls(i) for i in range(count)]  # type: ignore[call-arg]

    def resolved_logical_name(self) -> str:
        return f"gpu_nvidia_{self._gpu_index}_{type(self)._metric_suffix}"


class _NvidiaSmiVariantSensor(_NvidiaSmiGpuMixin, Sensor):
    """Intermediate base — no `logical_name` so the registry filters it."""

    priority: ClassVar[int] = 100
    icon: ClassVar[str | None] = "mdi:expansion-card"
    state_class: ClassVar[str | None] = "measurement"
    _smi_field: ClassVar[str] = ""

    @classmethod
    def probe(cls) -> bool:
        return _nvidia_smi_device_count() > 0

    async def _query_field(self) -> str | None:
        return await _nvidia_smi_query(type(self)._smi_field, self._gpu_index)


class GpuNvidiaTempNvidiaSmi(_NvidiaSmiVariantSensor):
    """GPU temperature via `nvidia-smi --query-gpu=temperature.gpu`."""

    logical_name: ClassVar[str] = "gpu_nvidia_temp"
    unit: ClassVar[str | None] = "°C"
    device_class: ClassVar[str | None] = "temperature"
    _metric_suffix: ClassVar[str] = "temp"
    _smi_field: ClassVar[str] = "temperature.gpu"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            raw = await self._query_field()
            if _is_na(raw):
                return SensorReading(value=None, timestamp=now, unavailable=True)
            return SensorReading(value=int(float(raw.strip())), timestamp=now)  # type: ignore[union-attr]
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class GpuNvidiaUtilizationNvidiaSmi(_NvidiaSmiVariantSensor):
    """GPU utilization via `nvidia-smi --query-gpu=utilization.gpu`."""

    logical_name: ClassVar[str] = "gpu_nvidia_utilization"
    unit: ClassVar[str | None] = "%"
    _metric_suffix: ClassVar[str] = "utilization"
    _smi_field: ClassVar[str] = "utilization.gpu"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            raw = await self._query_field()
            if _is_na(raw):
                return SensorReading(value=None, timestamp=now, unavailable=True)
            return SensorReading(value=int(float(raw.strip())), timestamp=now)  # type: ignore[union-attr]
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class GpuNvidiaMemoryUsedNvidiaSmi(_NvidiaSmiVariantSensor):
    """VRAM used (MB) via `nvidia-smi --query-gpu=memory.used`."""

    logical_name: ClassVar[str] = "gpu_nvidia_memory_used"
    unit: ClassVar[str | None] = "MB"
    _metric_suffix: ClassVar[str] = "memory_used"
    _smi_field: ClassVar[str] = "memory.used"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            raw = await self._query_field()
            if _is_na(raw):
                return SensorReading(value=None, timestamp=now, unavailable=True)
            return SensorReading(value=int(float(raw.strip())), timestamp=now)  # type: ignore[union-attr]
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class GpuNvidiaMemoryTotalNvidiaSmi(_NvidiaSmiVariantSensor):
    """VRAM total (MB) via `nvidia-smi --query-gpu=memory.total`.

    Capacity does not change at runtime, so `state_class=None`.
    """

    logical_name: ClassVar[str] = "gpu_nvidia_memory_total"
    unit: ClassVar[str | None] = "MB"
    state_class: ClassVar[str | None] = None
    _metric_suffix: ClassVar[str] = "memory_total"
    _smi_field: ClassVar[str] = "memory.total"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            raw = await self._query_field()
            if _is_na(raw):
                return SensorReading(value=None, timestamp=now, unavailable=True)
            return SensorReading(value=int(float(raw.strip())), timestamp=now)  # type: ignore[union-attr]
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class GpuNvidiaPowerDrawNvidiaSmi(_NvidiaSmiVariantSensor):
    """Power draw (W) via `nvidia-smi --query-gpu=power.draw`."""

    logical_name: ClassVar[str] = "gpu_nvidia_power_draw"
    unit: ClassVar[str | None] = "W"
    device_class: ClassVar[str | None] = "power"
    _metric_suffix: ClassVar[str] = "power_draw"
    _smi_field: ClassVar[str] = "power.draw"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            raw = await self._query_field()
            if _is_na(raw):
                return SensorReading(value=None, timestamp=now, unavailable=True)
            return SensorReading(value=float(raw.strip()), timestamp=now)  # type: ignore[union-attr]
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class GpuNvidiaFanSpeedNvidiaSmi(_NvidiaSmiVariantSensor):
    """Fan speed (%) via `nvidia-smi --query-gpu=fan.speed`.

    Cards without a controllable fan report `[N/A]`; treated as unavailable.
    """

    logical_name: ClassVar[str] = "gpu_nvidia_fan_speed"
    unit: ClassVar[str | None] = "%"
    _metric_suffix: ClassVar[str] = "fan_speed"
    _smi_field: ClassVar[str] = "fan.speed"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            raw = await self._query_field()
            if _is_na(raw):
                return SensorReading(value=None, timestamp=now, unavailable=True)
            return SensorReading(value=int(float(raw.strip())), timestamp=now)  # type: ignore[union-attr]
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)
