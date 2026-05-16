"""Tests for NVIDIA GPU sensors.

Most CI hosts have no NVIDIA GPU, so the bulk of these tests focus on:
- helpers behaving sanely on a no-GPU host
- probe semantics matching the helper return values
- per-instance class wiring (resolved name templates, class attributes)
- registry discoverability (the six logical names per variant strategy)

The few live-collect assertions skip themselves cleanly when neither variant
probes True.
"""

from __future__ import annotations

import pytest

from system_sensors.registry import discover_sensors
from system_sensors.sensors.base import SensorReading
from system_sensors.sensors.gpu_nvidia import (
    GpuNvidiaFanSpeedNvidiaSmi,
    GpuNvidiaFanSpeedNvml,
    GpuNvidiaMemoryTotalNvidiaSmi,
    GpuNvidiaMemoryTotalNvml,
    GpuNvidiaMemoryUsedNvidiaSmi,
    GpuNvidiaMemoryUsedNvml,
    GpuNvidiaPowerDrawNvidiaSmi,
    GpuNvidiaPowerDrawNvml,
    GpuNvidiaTempNvidiaSmi,
    GpuNvidiaTempNvml,
    GpuNvidiaUtilizationNvidiaSmi,
    GpuNvidiaUtilizationNvml,
    _is_na,
    _nvidia_smi_device_count,
    _nvml_device_count,
)

_NVML_CLASSES = (
    GpuNvidiaTempNvml,
    GpuNvidiaUtilizationNvml,
    GpuNvidiaMemoryUsedNvml,
    GpuNvidiaMemoryTotalNvml,
    GpuNvidiaPowerDrawNvml,
    GpuNvidiaFanSpeedNvml,
)

_SMI_CLASSES = (
    GpuNvidiaTempNvidiaSmi,
    GpuNvidiaUtilizationNvidiaSmi,
    GpuNvidiaMemoryUsedNvidiaSmi,
    GpuNvidiaMemoryTotalNvidiaSmi,
    GpuNvidiaPowerDrawNvidiaSmi,
    GpuNvidiaFanSpeedNvidiaSmi,
)

_EXPECTED_LOGICAL_NAMES = {
    "gpu_nvidia_temp",
    "gpu_nvidia_utilization",
    "gpu_nvidia_memory_used",
    "gpu_nvidia_memory_total",
    "gpu_nvidia_power_draw",
    "gpu_nvidia_fan_speed",
}


def test_nvml_device_count_returns_non_negative_int() -> None:
    count = _nvml_device_count()
    assert isinstance(count, int)
    assert count >= 0


def test_nvidia_smi_device_count_returns_non_negative_int() -> None:
    count = _nvidia_smi_device_count()
    assert isinstance(count, int)
    assert count >= 0


def test_nvml_probes_match_device_count() -> None:
    """Every NVML variant's probe must agree with `_nvml_device_count() > 0`."""
    expected = _nvml_device_count() > 0
    for cls in _NVML_CLASSES:
        assert cls.probe() is expected


def test_nvidia_smi_probes_match_device_count() -> None:
    """Every nvidia-smi variant's probe must agree with the smi device count."""
    expected = _nvidia_smi_device_count() > 0
    for cls in _SMI_CLASSES:
        assert cls.probe() is expected


def test_resolved_logical_name_nvml() -> None:
    """Constructed NVML instance names follow the `gpu_nvidia_<i>_<metric>` template."""
    assert GpuNvidiaTempNvml(0).resolved_logical_name() == "gpu_nvidia_0_temp"
    assert (
        GpuNvidiaUtilizationNvml(1).resolved_logical_name()
        == "gpu_nvidia_1_utilization"
    )
    assert (
        GpuNvidiaMemoryUsedNvml(2).resolved_logical_name()
        == "gpu_nvidia_2_memory_used"
    )
    assert (
        GpuNvidiaMemoryTotalNvml(0).resolved_logical_name()
        == "gpu_nvidia_0_memory_total"
    )
    assert (
        GpuNvidiaPowerDrawNvml(3).resolved_logical_name()
        == "gpu_nvidia_3_power_draw"
    )
    assert (
        GpuNvidiaFanSpeedNvml(0).resolved_logical_name()
        == "gpu_nvidia_0_fan_speed"
    )


def test_resolved_logical_name_nvidia_smi() -> None:
    """Constructed nvidia-smi instance names follow the same template."""
    assert (
        GpuNvidiaTempNvidiaSmi(0).resolved_logical_name() == "gpu_nvidia_0_temp"
    )
    assert (
        GpuNvidiaFanSpeedNvidiaSmi(2).resolved_logical_name()
        == "gpu_nvidia_2_fan_speed"
    )


def test_each_class_is_discoverable() -> None:
    """All twelve concrete variant classes must appear in `discover_sensors()`."""
    discovered = set(discover_sensors())
    for cls in _NVML_CLASSES + _SMI_CLASSES:
        assert cls in discovered, f"{cls.__qualname__} missing from discovery"


def test_every_expected_logical_name_is_discovered() -> None:
    """The six template logical names show up at least once (each is shared
    between an NVML and an nvidia-smi variant)."""
    discovered = discover_sensors()
    names = {getattr(c, "logical_name", None) for c in discovered}
    for expected in _EXPECTED_LOGICAL_NAMES:
        assert expected in names, f"logical_name {expected!r} not discovered"


def test_class_attribute_units_match_spec() -> None:
    """Spot-check the canonical unit + device_class for every concrete variant."""
    for cls in (GpuNvidiaTempNvml, GpuNvidiaTempNvidiaSmi):
        assert cls.unit == "°C"
        assert cls.device_class == "temperature"
    for cls in (GpuNvidiaUtilizationNvml, GpuNvidiaUtilizationNvidiaSmi):
        assert cls.unit == "%"
        assert cls.device_class is None
    for cls in (
        GpuNvidiaMemoryUsedNvml,
        GpuNvidiaMemoryUsedNvidiaSmi,
        GpuNvidiaMemoryTotalNvml,
        GpuNvidiaMemoryTotalNvidiaSmi,
    ):
        assert cls.unit == "MB"
    for cls in (GpuNvidiaPowerDrawNvml, GpuNvidiaPowerDrawNvidiaSmi):
        assert cls.unit == "W"
        assert cls.device_class == "power"
    for cls in (GpuNvidiaFanSpeedNvml, GpuNvidiaFanSpeedNvidiaSmi):
        assert cls.unit == "%"


def test_state_class_measurement_except_memory_total() -> None:
    """All metrics carry `state_class='measurement'` except `memory_total`."""
    for cls in (
        GpuNvidiaTempNvml,
        GpuNvidiaUtilizationNvml,
        GpuNvidiaMemoryUsedNvml,
        GpuNvidiaPowerDrawNvml,
        GpuNvidiaFanSpeedNvml,
        GpuNvidiaTempNvidiaSmi,
        GpuNvidiaUtilizationNvidiaSmi,
        GpuNvidiaMemoryUsedNvidiaSmi,
        GpuNvidiaPowerDrawNvidiaSmi,
        GpuNvidiaFanSpeedNvidiaSmi,
    ):
        assert cls.state_class == "measurement", cls.__qualname__
    assert GpuNvidiaMemoryTotalNvml.state_class is None
    assert GpuNvidiaMemoryTotalNvidiaSmi.state_class is None


def test_priorities_favor_nvml_over_nvidia_smi() -> None:
    """NVML variants must outrank nvidia-smi variants per metric."""
    for nvml_cls, smi_cls in zip(_NVML_CLASSES, _SMI_CLASSES):
        assert nvml_cls.logical_name == smi_cls.logical_name
        assert nvml_cls.priority > smi_cls.priority


def test_icon_is_expansion_card_for_all() -> None:
    """All twelve classes share the `mdi:expansion-card` icon."""
    for cls in _NVML_CLASSES + _SMI_CLASSES:
        assert cls.icon == "mdi:expansion-card"


def test_is_na_helper_recognizes_sentinel_values() -> None:
    assert _is_na(None) is True
    assert _is_na("") is True
    assert _is_na("   ") is True
    assert _is_na("[N/A]") is True
    assert _is_na("[Not Supported]") is True  # `[N` prefix is enough
    assert _is_na("42") is False
    assert _is_na("3.14") is False


@pytest.mark.skipif(
    _nvml_device_count() == 0,
    reason="no NVML-visible NVIDIA GPU on this host",
)
async def test_nvml_live_collect_returns_reading() -> None:
    """Live collect call against the real NVML stack. Skipped without a GPU."""
    for cls in _NVML_CLASSES:
        for inst in cls.enumerate_instances():
            reading = await inst.collect()
            assert isinstance(reading, SensorReading)


@pytest.mark.skipif(
    _nvidia_smi_device_count() == 0,
    reason="no nvidia-smi-visible NVIDIA GPU on this host",
)
async def test_nvidia_smi_live_collect_returns_reading() -> None:
    """Live collect call against `nvidia-smi`. Skipped without a GPU."""
    for cls in _SMI_CLASSES:
        for inst in cls.enumerate_instances():
            reading = await inst.collect()
            assert isinstance(reading, SensorReading)
