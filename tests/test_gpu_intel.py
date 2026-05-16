"""Tests for Intel GPU sysfs hwmon sensors.

Same shape as `test_gpu_amd.py` but for the two Intel logical names and the
`i915` / `xe` driver tuple. Most CI hosts have no Intel discrete GPU and i915
on integrated graphics often omits ``temp1_input``, so the no-device
assertions skip themselves cleanly when an Intel card IS present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from system_sensors.registry import discover_sensors
from system_sensors.sensors import _drm_hwmon
from system_sensors.sensors._drm_hwmon import (
    enumerate_drm_cards_for_driver_with_file,
)
from system_sensors.sensors.gpu_intel import (
    GpuIntelPowerDraw,
    GpuIntelTemp,
)

_INTEL_CLASSES = (GpuIntelTemp, GpuIntelPowerDraw)

_EXPECTED_LOGICAL_NAMES = {
    "gpu_intel_temp",
    "gpu_intel_power_draw",
}


def _host_has_intel_gpu_temp() -> bool:
    return bool(
        enumerate_drm_cards_for_driver_with_file(("i915", "xe"), "temp1_input")
    )


def _host_has_intel_gpu_power() -> bool:
    return bool(
        enumerate_drm_cards_for_driver_with_file(("i915", "xe"), "power1_average")
    )


def test_each_class_is_discoverable() -> None:
    discovered = set(discover_sensors())
    for cls in _INTEL_CLASSES:
        assert cls in discovered, f"{cls.__qualname__} missing from discovery"


def test_every_expected_logical_name_is_discovered() -> None:
    discovered = discover_sensors()
    names = {getattr(c, "logical_name", None) for c in discovered}
    for expected in _EXPECTED_LOGICAL_NAMES:
        assert expected in names, f"logical_name {expected!r} not discovered"


@pytest.mark.skipif(
    _host_has_intel_gpu_temp(),
    reason="host exposes an Intel GPU temp via hwmon — skip no-device assertion",
)
def test_temp_probe_false_on_host_without_intel_temp() -> None:
    assert GpuIntelTemp.probe() is False


@pytest.mark.skipif(
    _host_has_intel_gpu_power(),
    reason="host exposes Intel GPU power via hwmon — skip no-device assertion",
)
def test_power_probe_false_on_host_without_intel_power() -> None:
    assert GpuIntelPowerDraw.probe() is False


@pytest.mark.skipif(
    _host_has_intel_gpu_temp(),
    reason="host exposes an Intel GPU temp via hwmon — skip no-device assertion",
)
def test_temp_enumerate_empty_on_host_without_intel_temp() -> None:
    assert GpuIntelTemp.enumerate_instances() == []


@pytest.mark.skipif(
    _host_has_intel_gpu_power(),
    reason="host exposes Intel GPU power via hwmon — skip no-device assertion",
)
def test_power_enumerate_empty_on_host_without_intel_power() -> None:
    assert GpuIntelPowerDraw.enumerate_instances() == []


def test_resolved_logical_name_template() -> None:
    """Manually constructed instances produce `gpu_intel_<idx>_<metric>`."""
    fake = Path("/tmp/does-not-exist")
    assert GpuIntelTemp(0, fake).resolved_logical_name() == "gpu_intel_0_temp"
    assert (
        GpuIntelTemp(2, fake).resolved_logical_name() == "gpu_intel_2_temp"
    )
    assert (
        GpuIntelPowerDraw(0, fake).resolved_logical_name()
        == "gpu_intel_0_power_draw"
    )
    assert (
        GpuIntelPowerDraw(1, fake).resolved_logical_name()
        == "gpu_intel_1_power_draw"
    )


def test_class_attribute_units_match_spec() -> None:
    assert GpuIntelTemp.unit == "°C"
    assert GpuIntelTemp.device_class == "temperature"
    assert GpuIntelPowerDraw.unit == "W"
    assert GpuIntelPowerDraw.device_class == "power"


def test_state_class_measurement_for_all() -> None:
    for cls in _INTEL_CLASSES:
        assert cls.state_class == "measurement", cls.__qualname__


def test_icon_is_expansion_card_for_all() -> None:
    for cls in _INTEL_CLASSES:
        assert cls.icon == "mdi:expansion-card"


def test_priority_is_100_for_all() -> None:
    for cls in _INTEL_CLASSES:
        assert cls.priority == 100


def test_enumerate_drm_helper_accepts_i915_and_xe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both `i915` and `xe` drivers are accepted by the Intel enumeration."""
    drm = tmp_path / "drm"

    card0 = drm / "card0"
    hwmon0 = card0 / "device" / "hwmon" / "hwmon0"
    hwmon0.mkdir(parents=True)
    (card0 / "device" / "uevent").write_text("DRIVER=i915\n", encoding="utf-8")
    (hwmon0 / "temp1_input").write_text("50000\n", encoding="utf-8")

    card1 = drm / "card1"
    hwmon1 = card1 / "device" / "hwmon" / "hwmon1"
    hwmon1.mkdir(parents=True)
    (card1 / "device" / "uevent").write_text("DRIVER=xe\n", encoding="utf-8")
    (hwmon1 / "temp1_input").write_text("55000\n", encoding="utf-8")

    monkeypatch.setattr(_drm_hwmon, "_DRM_BASE", drm)
    found = enumerate_drm_cards_for_driver_with_file(("i915", "xe"), "temp1_input")
    assert found == [(0, hwmon0), (1, hwmon1)]
