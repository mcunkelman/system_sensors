"""Tests for sensor discovery, probe gathering, variant selection, and instantiation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import pytest

from system_sensors.registry import (
    discover_sensors,
    instantiate_active_sensors,
    probe_all,
    select_variants,
)
from system_sensors.sensors.base import Sensor, SensorReading
from system_sensors.sensors.cpu import CpuLoad1m, CpuLoad5m, CpuLoad15m


def test_discover_sensors_finds_cpu_load_classes() -> None:
    """The three `CpuLoad*` classes must be discovered automatically."""
    discovered = discover_sensors()
    assert CpuLoad1m in discovered
    assert CpuLoad5m in discovered
    assert CpuLoad15m in discovered


def test_discover_sensors_excludes_intermediate_base() -> None:
    """`_CpuLoadBase` provides shared logic but has no `logical_name` — excluded."""
    from system_sensors.sensors.cpu import _CpuLoadBase

    assert _CpuLoadBase not in discover_sensors()


def test_discover_sensors_order_is_stable() -> None:
    """Calling `discover_sensors()` twice returns the same sequence."""
    assert discover_sensors() == discover_sensors()


def test_probe_all_groups_cpu_load_under_distinct_names() -> None:
    """Each `CpuLoad*` has its own `logical_name` — one entry per class."""
    probed = probe_all([CpuLoad1m, CpuLoad5m, CpuLoad15m])
    assert set(probed.keys()) == {"load_1m", "load_5m", "load_15m"}
    assert probed["load_1m"] == [CpuLoad1m]
    assert probed["load_5m"] == [CpuLoad5m]
    assert probed["load_15m"] == [CpuLoad15m]


def test_probe_all_swallows_probe_exceptions() -> None:
    """A probe that raises must be logged and treated as False, not crash."""

    class _ExplodingSensor(Sensor):
        logical_name: ClassVar[str] = "exploding"

        @classmethod
        def probe(cls) -> bool:
            raise RuntimeError("probe is buggy on purpose")

        async def collect(self) -> SensorReading:  # pragma: no cover - never run
            return SensorReading(value=None, timestamp=datetime.now(timezone.utc))

    probed = probe_all([_ExplodingSensor])
    assert probed == {}


def test_probe_all_sorts_by_priority_descending() -> None:
    """Highest-priority variant comes first within a logical_name group."""

    class _LowPri(Sensor):
        logical_name: ClassVar[str] = "shared"
        priority: ClassVar[int] = 10

        @classmethod
        def probe(cls) -> bool:
            return True

        async def collect(self) -> SensorReading:  # pragma: no cover
            return SensorReading(value=None, timestamp=datetime.now(timezone.utc))

    class _HighPri(Sensor):
        logical_name: ClassVar[str] = "shared"
        priority: ClassVar[int] = 100

        @classmethod
        def probe(cls) -> bool:
            return True

        async def collect(self) -> SensorReading:  # pragma: no cover
            return SensorReading(value=None, timestamp=datetime.now(timezone.utc))

    probed = probe_all([_LowPri, _HighPri])
    assert probed["shared"] == [_HighPri, _LowPri]


def test_select_variants_picks_all_three_load_classes() -> None:
    """Each load horizon owns its own `logical_name`, so all three survive."""
    probed = probe_all([CpuLoad1m, CpuLoad5m, CpuLoad15m])
    selected = select_variants(probed)
    assert selected == {
        "load_1m": CpuLoad1m,
        "load_5m": CpuLoad5m,
        "load_15m": CpuLoad15m,
    }


def test_select_variants_picks_highest_priority_for_shared_name() -> None:
    """When two variants share a logical_name, the higher priority wins."""

    class _LowPri(Sensor):
        logical_name: ClassVar[str] = "shared_pick"
        priority: ClassVar[int] = 10

        @classmethod
        def probe(cls) -> bool:
            return True

        async def collect(self) -> SensorReading:  # pragma: no cover
            return SensorReading(value=None, timestamp=datetime.now(timezone.utc))

    class _HighPri(Sensor):
        logical_name: ClassVar[str] = "shared_pick"
        priority: ClassVar[int] = 100

        @classmethod
        def probe(cls) -> bool:
            return True

        async def collect(self) -> SensorReading:  # pragma: no cover
            return SensorReading(value=None, timestamp=datetime.now(timezone.utc))

    probed = probe_all([_LowPri, _HighPri])
    selected = select_variants(probed)
    assert selected == {"shared_pick": _HighPri}


def test_instantiate_active_sensors_expands_each_class_once() -> None:
    """Default `enumerate_instances()` yields one instance per chosen class."""
    selected = {
        "load_1m": CpuLoad1m,
        "load_5m": CpuLoad5m,
        "load_15m": CpuLoad15m,
    }
    instances = instantiate_active_sensors(selected)
    assert len(instances) == 3
    resolved = sorted(i.resolved_logical_name() for i in instances)
    assert resolved == ["load_15m", "load_1m", "load_5m"]


def test_instantiate_active_sensors_rejects_duplicate_resolved_names() -> None:
    """Two classes resolving to the same name signal a multi-instance bug."""

    class _A(Sensor):
        logical_name: ClassVar[str] = "collider"

        @classmethod
        def probe(cls) -> bool:
            return True

        async def collect(self) -> SensorReading:  # pragma: no cover
            return SensorReading(value=None, timestamp=datetime.now(timezone.utc))

    class _B(Sensor):
        logical_name: ClassVar[str] = "collider_b"  # distinct class-level name

        @classmethod
        def probe(cls) -> bool:
            return True

        async def collect(self) -> SensorReading:  # pragma: no cover
            return SensorReading(value=None, timestamp=datetime.now(timezone.utc))

        def resolved_logical_name(self) -> str:
            # Pathologically returns the OTHER class's name — registry must catch.
            return "collider"

    with pytest.raises(ValueError, match="Duplicate resolved logical name"):
        instantiate_active_sensors({"a": _A, "b": _B})


def test_discover_sensors_includes_step_4a_psutil_classes() -> None:
    """After Step 4a, the registry must surface all psutil-based sensors."""
    discovered = discover_sensors()
    names = {getattr(c, "logical_name", None) for c in discovered}
    expected = {
        "load_1m",
        "load_5m",
        "load_15m",
        "cpu_usage",
        "clock_speed",
        "memory_use",
        "swap_usage",
        "disk_use",
        "hostname",
        "host_ip",
        "host_arch",
        "last_boot",
        "net_tx_total",
        "net_rx_total",
    }
    assert expected.issubset(names)
    assert len(discovered) >= 13


def test_discover_sensors_includes_step_4b_multivariant_classes() -> None:
    """Step 4b: host_os + updates + wifi_signal + wifi_ssid must be registered."""
    discovered = discover_sensors()
    names = {getattr(c, "logical_name", None) for c in discovered}
    assert "host_os" in names
    assert "updates" in names
    assert "wifi_signal" in names
    assert "wifi_ssid" in names


def test_discover_sensors_includes_step_4c_thermal_and_rpi_classes() -> None:
    """Step 4c: thermal + fan + rpi power_status must be registered.

    `cpu_temp_core` is a template logical name shared by all per-core variant
    classes; resolved per-instance names use `cpu_temp_core_<index>`.
    """
    discovered = discover_sensors()
    names = {getattr(c, "logical_name", None) for c in discovered}
    assert "cpu_temp_mean" in names
    assert "cpu_temp_max" in names
    assert "cpu_temp_core" in names
    assert "fan_speed" in names
    assert "power_status" in names


def test_discover_sensors_includes_step_4d_gpu_nvidia_classes() -> None:
    """Step 4d: every per-GPU NVIDIA logical name template must be registered.

    Six metrics, two variants each (NVML + nvidia-smi). The two variants share
    a logical_name per metric, so we assert the six templates appear.
    """
    discovered = discover_sensors()
    names = {getattr(c, "logical_name", None) for c in discovered}
    expected = {
        "gpu_nvidia_temp",
        "gpu_nvidia_utilization",
        "gpu_nvidia_memory_used",
        "gpu_nvidia_memory_total",
        "gpu_nvidia_power_draw",
        "gpu_nvidia_fan_speed",
    }
    assert expected.issubset(names)


def test_discover_sensors_includes_step_4e_gpu_amd_and_intel_classes() -> None:
    """Step 4e: every per-GPU AMD + Intel logical name template must be registered.

    AMD contributes five sysfs hwmon metrics; Intel contributes two. Each
    metric has exactly one variant (sysfs hwmon — no CLI fallback in v1).
    """
    discovered = discover_sensors()
    names = {getattr(c, "logical_name", None) for c in discovered}
    expected = {
        "gpu_amd_temp",
        "gpu_amd_temp_junction",
        "gpu_amd_temp_memory",
        "gpu_amd_fan_speed",
        "gpu_amd_power_draw",
        "gpu_intel_temp",
        "gpu_intel_power_draw",
    }
    assert expected.issubset(names)


def test_step_4b_wifi_resolved_names_match_iface_pattern() -> None:
    """If any wifi variant probes True, the instantiated sensors must name themselves
    `wifi_<iface>_signal` / `wifi_<iface>_ssid`."""
    from system_sensors.sensors.wifi import _find_wireless_interfaces

    ifaces = _find_wireless_interfaces()
    if not ifaces:
        pytest.skip("no wireless interfaces on this host")

    discovered = discover_sensors()
    probed = probe_all(discovered)
    selected = select_variants(probed)
    instances = instantiate_active_sensors(selected)
    resolved = {i.resolved_logical_name() for i in instances}
    for iface in ifaces:
        # Either one or both should appear depending on which variants probe True;
        # at minimum the wifi groups (if any selected) emit the expected pattern.
        sig = f"wifi_{iface}_signal"
        ssid = f"wifi_{iface}_ssid"
        if "wifi_signal" in selected:
            assert sig in resolved
        if "wifi_ssid" in selected:
            assert ssid in resolved
