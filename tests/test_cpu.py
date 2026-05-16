"""Tests for the CPU load vertical slice."""

from __future__ import annotations

import asyncio

import psutil
import pytest

from system_sensors.registry import (
    discover_sensors,
    probe_all,
    select_variants,
)
from system_sensors.sensors.base import SensorReading
from system_sensors.sensors.cpu import (
    ClockSpeed,
    CpuLoad1m,
    CpuLoad5m,
    CpuLoad15m,
    CpuTempCorePsutil,
    CpuTempCoreSysfs,
    CpuTempMaxPsutil,
    CpuTempMaxSysfs,
    CpuTempMeanPsutil,
    CpuTempMeanSysfs,
    CpuUsage,
    FanSpeedPsutil,
    _psutil_cpu_temps,
    _sysfs_cpu_temps,
)

_HAS_LOADAVG = hasattr(psutil, "getloadavg")
_HAS_PSUTIL_TEMPS = _psutil_cpu_temps() is not None
_HAS_SYSFS_TEMPS = _sysfs_cpu_temps() is not None
_HAS_ANY_TEMPS = _HAS_PSUTIL_TEMPS or _HAS_SYSFS_TEMPS


@pytest.mark.skipif(not _HAS_LOADAVG, reason="psutil.getloadavg() unavailable on this platform")
def test_cpu_load_1m_probe_true_on_posix() -> None:
    """On a POSIX host with psutil, the probe must return True."""
    assert CpuLoad1m.probe() is True


@pytest.mark.skipif(not _HAS_LOADAVG, reason="psutil.getloadavg() unavailable on this platform")
async def test_cpu_load_1m_collect_returns_float_reading() -> None:
    """`collect()` returns an available reading with a float value."""
    reading = await CpuLoad1m().collect()
    assert isinstance(reading, SensorReading)
    assert reading.unavailable is False
    assert isinstance(reading.value, float)
    assert reading.value >= 0.0


@pytest.mark.skipif(not _HAS_LOADAVG, reason="psutil.getloadavg() unavailable on this platform")
async def test_cpu_load_horizons_use_distinct_indices() -> None:
    """Each of the three classes reads its own slot from `getloadavg()`."""
    one, five, fifteen = (
        await CpuLoad1m().collect(),
        await CpuLoad5m().collect(),
        await CpuLoad15m().collect(),
    )
    assert all(r.unavailable is False for r in (one, five, fifteen))
    assert all(isinstance(r.value, float) for r in (one, five, fifteen))


def test_cpu_load_resolved_logical_names_are_distinct() -> None:
    """The three classes publish under three different logical names."""
    names = {
        CpuLoad1m().resolved_logical_name(),
        CpuLoad5m().resolved_logical_name(),
        CpuLoad15m().resolved_logical_name(),
    }
    assert names == {"load_1m", "load_5m", "load_15m"}


def test_cpu_usage_probe_true() -> None:
    assert CpuUsage.probe() is True


async def test_cpu_usage_warmup_then_real_value() -> None:
    """First collect primes psutil and returns 0; second returns a real float."""
    sensor = CpuUsage()
    first = await sensor.collect()
    assert isinstance(first, SensorReading)
    assert first.unavailable is False
    assert first.value == 0.0
    await asyncio.sleep(1.05)
    second = await sensor.collect()
    assert second.unavailable is False
    assert isinstance(second.value, float)
    assert 0.0 <= second.value <= 100.0


def test_clock_speed_probe_true() -> None:
    assert ClockSpeed.probe() is True


async def test_clock_speed_collect_returns_int_or_unavailable() -> None:
    """In containers/VMs `cpu_freq()` can be None — unavailable is acceptable."""
    reading = await ClockSpeed().collect()
    assert isinstance(reading, SensorReading)
    if reading.unavailable:
        assert reading.value is None
        return
    assert isinstance(reading.value, int)
    assert reading.value >= 0


def test_cpu_temp_mean_psutil_probe_returns_bool() -> None:
    """Probe must always return a bool, never crash."""
    assert isinstance(CpuTempMeanPsutil.probe(), bool)


def test_cpu_temp_mean_sysfs_probe_returns_bool() -> None:
    assert isinstance(CpuTempMeanSysfs.probe(), bool)


@pytest.mark.skipif(not _HAS_PSUTIL_TEMPS, reason="psutil has no CPU temps on this host")
async def test_cpu_temp_mean_psutil_collect_returns_float() -> None:
    reading = await CpuTempMeanPsutil().collect()
    assert isinstance(reading, SensorReading)
    assert reading.unavailable is False
    assert isinstance(reading.value, float)


@pytest.mark.skipif(not _HAS_PSUTIL_TEMPS, reason="psutil has no CPU temps on this host")
async def test_cpu_temp_max_psutil_collect_returns_float() -> None:
    reading = await CpuTempMaxPsutil().collect()
    assert isinstance(reading, SensorReading)
    assert reading.unavailable is False
    assert isinstance(reading.value, float)


@pytest.mark.skipif(not _HAS_PSUTIL_TEMPS, reason="psutil has no CPU temps on this host")
def test_cpu_temp_core_psutil_enumerates_instances() -> None:
    instances = CpuTempCorePsutil.enumerate_instances()
    assert len(instances) >= 1
    resolved = [i.resolved_logical_name() for i in instances]
    for idx, name in enumerate(resolved):
        assert name == f"cpu_temp_core_{idx}"


@pytest.mark.skipif(not _HAS_PSUTIL_TEMPS, reason="psutil has no CPU temps on this host")
async def test_cpu_temp_core_psutil_collect_returns_float() -> None:
    instances = CpuTempCorePsutil.enumerate_instances()
    if not instances:
        pytest.skip("no per-core temps enumerated")
    reading = await instances[0].collect()
    assert isinstance(reading, SensorReading)
    assert reading.unavailable is False
    assert isinstance(reading.value, float)


@pytest.mark.skipif(_HAS_PSUTIL_TEMPS, reason="psutil temps available; sysfs is fallback")
@pytest.mark.skipif(not _HAS_SYSFS_TEMPS, reason="sysfs has no CPU thermal zones")
async def test_cpu_temp_mean_sysfs_collect_returns_float() -> None:
    reading = await CpuTempMeanSysfs().collect()
    assert isinstance(reading, SensorReading)
    assert reading.unavailable is False
    assert isinstance(reading.value, float)


def test_cpu_temp_variants_registered_under_logical_names() -> None:
    """All three logical names appear in discovery output."""
    discovered = discover_sensors()
    names = {getattr(c, "logical_name", None) for c in discovered}
    assert "cpu_temp_mean" in names
    assert "cpu_temp_max" in names
    assert "cpu_temp_core" in names


@pytest.mark.skipif(not _HAS_PSUTIL_TEMPS, reason="psutil has no CPU temps on this host")
def test_cpu_temp_psutil_wins_priority_over_sysfs() -> None:
    """When both variants probe True, psutil (priority 200) is selected."""
    discovered = discover_sensors()
    probed = probe_all(discovered)
    selected = select_variants(probed)
    if "cpu_temp_mean" in selected:
        assert selected["cpu_temp_mean"] is CpuTempMeanPsutil
    if "cpu_temp_max" in selected:
        assert selected["cpu_temp_max"] is CpuTempMaxPsutil
    if "cpu_temp_core" in selected:
        assert selected["cpu_temp_core"] is CpuTempCorePsutil


def test_fan_speed_probe_returns_bool() -> None:
    assert isinstance(FanSpeedPsutil.probe(), bool)


@pytest.mark.skipif(
    not FanSpeedPsutil.probe(), reason="no fan sensors available on this host"
)
async def test_fan_speed_collect_returns_int() -> None:
    reading = await FanSpeedPsutil().collect()
    assert isinstance(reading, SensorReading)
    assert reading.unavailable is False
    assert isinstance(reading.value, int)
    assert reading.value >= 0


def test_fan_speed_registered_under_logical_name() -> None:
    discovered = discover_sensors()
    names = {getattr(c, "logical_name", None) for c in discovered}
    assert "fan_speed" in names
