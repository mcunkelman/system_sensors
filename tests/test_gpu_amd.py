"""Tests for AMD GPU sysfs hwmon sensors.

CI hosts have no amdgpu device, so most assertions focus on:
- registry discoverability of all five logical names
- probe() returning False when /sys/class/drm has no amdgpu card
- enumerate_instances() returning an empty list under the same condition
- resolved_logical_name() formatting when an instance is manually constructed
- class-attribute sanity (units, device_class, priority, icon)

A separate fake-filesystem test exercises the shared
`enumerate_drm_cards_for_driver_with_file` helper end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from system_sensors.registry import discover_sensors
from system_sensors.sensors import _drm_hwmon
from system_sensors.sensors._drm_hwmon import (
    enumerate_drm_cards_for_driver_with_file,
)
from system_sensors.sensors.gpu_amd import (
    GpuAmdFanSpeed,
    GpuAmdPowerDraw,
    GpuAmdTemp,
    GpuAmdTempJunction,
    GpuAmdTempMemory,
)

_AMD_CLASSES = (
    GpuAmdTemp,
    GpuAmdTempJunction,
    GpuAmdTempMemory,
    GpuAmdFanSpeed,
    GpuAmdPowerDraw,
)

_EXPECTED_LOGICAL_NAMES = {
    "gpu_amd_temp",
    "gpu_amd_temp_junction",
    "gpu_amd_temp_memory",
    "gpu_amd_fan_speed",
    "gpu_amd_power_draw",
}


def _host_has_amdgpu() -> bool:
    return bool(enumerate_drm_cards_for_driver_with_file(("amdgpu",), "temp1_input"))


def test_each_class_is_discoverable() -> None:
    discovered = set(discover_sensors())
    for cls in _AMD_CLASSES:
        assert cls in discovered, f"{cls.__qualname__} missing from discovery"


def test_every_expected_logical_name_is_discovered() -> None:
    discovered = discover_sensors()
    names = {getattr(c, "logical_name", None) for c in discovered}
    for expected in _EXPECTED_LOGICAL_NAMES:
        assert expected in names, f"logical_name {expected!r} not discovered"


@pytest.mark.skipif(
    _host_has_amdgpu(),
    reason="host has an amdgpu card — skip the no-device assertions",
)
def test_probe_false_on_host_without_amdgpu() -> None:
    """Without an amdgpu card every variant probes False."""
    for cls in _AMD_CLASSES:
        assert cls.probe() is False, cls.__qualname__


@pytest.mark.skipif(
    _host_has_amdgpu(),
    reason="host has an amdgpu card — skip the no-device assertions",
)
def test_enumerate_instances_empty_on_host_without_amdgpu() -> None:
    """No amdgpu card -> enumerate_instances() returns []."""
    for cls in _AMD_CLASSES:
        assert cls.enumerate_instances() == [], cls.__qualname__


def test_resolved_logical_name_template() -> None:
    """Manually constructed instances produce `gpu_amd_<idx>_<metric>`."""
    fake = Path("/tmp/does-not-exist")
    assert GpuAmdTemp(0, fake).resolved_logical_name() == "gpu_amd_0_temp"
    assert (
        GpuAmdTempJunction(1, fake).resolved_logical_name()
        == "gpu_amd_1_temp_junction"
    )
    assert (
        GpuAmdTempMemory(2, fake).resolved_logical_name()
        == "gpu_amd_2_temp_memory"
    )
    assert (
        GpuAmdFanSpeed(0, fake).resolved_logical_name()
        == "gpu_amd_0_fan_speed"
    )
    assert (
        GpuAmdPowerDraw(3, fake).resolved_logical_name()
        == "gpu_amd_3_power_draw"
    )


def test_class_attribute_units_match_spec() -> None:
    for cls in (GpuAmdTemp, GpuAmdTempJunction, GpuAmdTempMemory):
        assert cls.unit == "°C"
        assert cls.device_class == "temperature"
    assert GpuAmdFanSpeed.unit == "RPM"
    assert GpuAmdFanSpeed.device_class is None
    assert GpuAmdPowerDraw.unit == "W"
    assert GpuAmdPowerDraw.device_class == "power"


def test_state_class_measurement_for_all() -> None:
    for cls in _AMD_CLASSES:
        assert cls.state_class == "measurement", cls.__qualname__


def test_icon_is_expansion_card_for_all() -> None:
    for cls in _AMD_CLASSES:
        assert cls.icon == "mdi:expansion-card"


def test_priority_is_100_for_all() -> None:
    for cls in _AMD_CLASSES:
        assert cls.priority == 100


def test_enumerate_drm_helper_finds_fake_amdgpu_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Build a tmp /sys/class/drm hierarchy and assert the helper finds it.

    Layout:
        card0/device/uevent          -> DRIVER=amdgpu
        card0/device/hwmon/hwmon3/temp1_input -> "45000"

    Helper should return `[(0, .../hwmon3)]`.
    """
    drm = tmp_path / "drm"
    card = drm / "card0"
    hwmon = card / "device" / "hwmon" / "hwmon3"
    hwmon.mkdir(parents=True)
    (card / "device" / "uevent").write_text(
        "DRIVER=amdgpu\nPCI_SLOT_NAME=0000:01:00.0\n", encoding="utf-8"
    )
    (hwmon / "temp1_input").write_text("45000\n", encoding="utf-8")

    monkeypatch.setattr(_drm_hwmon, "_DRM_BASE", drm)
    found = enumerate_drm_cards_for_driver_with_file(("amdgpu",), "temp1_input")
    assert found == [(0, hwmon)]


def test_enumerate_drm_helper_filters_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cards matching the driver but lacking the requested file are filtered out."""
    drm = tmp_path / "drm"
    card = drm / "card0"
    hwmon = card / "device" / "hwmon" / "hwmon0"
    hwmon.mkdir(parents=True)
    (card / "device" / "uevent").write_text("DRIVER=amdgpu\n", encoding="utf-8")
    # No temp1_input file written.

    monkeypatch.setattr(_drm_hwmon, "_DRM_BASE", drm)
    assert (
        enumerate_drm_cards_for_driver_with_file(("amdgpu",), "temp1_input") == []
    )


def test_enumerate_drm_helper_filters_other_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cards with a non-matching DRIVER value are filtered out."""
    drm = tmp_path / "drm"
    card = drm / "card0"
    hwmon = card / "device" / "hwmon" / "hwmon0"
    hwmon.mkdir(parents=True)
    (card / "device" / "uevent").write_text("DRIVER=nouveau\n", encoding="utf-8")
    (hwmon / "temp1_input").write_text("40000\n", encoding="utf-8")

    monkeypatch.setattr(_drm_hwmon, "_DRM_BASE", drm)
    assert (
        enumerate_drm_cards_for_driver_with_file(("amdgpu",), "temp1_input") == []
    )
