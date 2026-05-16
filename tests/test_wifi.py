"""Tests for Wi-Fi sensors."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from system_sensors.sensors.base import SensorReading
from system_sensors.sensors.wifi import (
    WifiSignalIw,
    WifiSignalNmcli,
    WifiSignalProc,
    WifiSsidIw,
    WifiSsidIwgetid,
    WifiSsidNmcli,
    _find_wireless_interfaces,
)


def test_find_wireless_interfaces_returns_sorted_list() -> None:
    found = _find_wireless_interfaces()
    assert isinstance(found, list)
    assert found == sorted(found)
    for iface in found:
        assert isinstance(iface, str)
        assert iface


def test_signal_iw_probe_matches_dependencies() -> None:
    expected = shutil.which("iw") is not None and bool(_find_wireless_interfaces())
    assert WifiSignalIw.probe() is expected


def test_signal_nmcli_probe_matches_dependencies() -> None:
    expected = shutil.which("nmcli") is not None and bool(_find_wireless_interfaces())
    assert WifiSignalNmcli.probe() is expected


def test_signal_proc_probe_matches_dependencies() -> None:
    expected = Path("/proc/net/wireless").exists() and bool(_find_wireless_interfaces())
    assert WifiSignalProc.probe() is expected


def test_ssid_iw_probe_matches_dependencies() -> None:
    expected = shutil.which("iw") is not None and bool(_find_wireless_interfaces())
    assert WifiSsidIw.probe() is expected


def test_ssid_nmcli_probe_matches_dependencies() -> None:
    expected = shutil.which("nmcli") is not None and bool(_find_wireless_interfaces())
    assert WifiSsidNmcli.probe() is expected


def test_ssid_iwgetid_probe_matches_dependencies() -> None:
    expected = shutil.which("iwgetid") is not None and bool(_find_wireless_interfaces())
    assert WifiSsidIwgetid.probe() is expected


def test_enumerate_instances_one_per_iface() -> None:
    ifaces = _find_wireless_interfaces()
    for cls in (
        WifiSignalIw,
        WifiSignalNmcli,
        WifiSignalProc,
        WifiSsidIw,
        WifiSsidNmcli,
        WifiSsidIwgetid,
    ):
        instances = cls.enumerate_instances()
        assert len(instances) == len(ifaces)
        resolved = [i.resolved_logical_name() for i in instances]
        assert resolved == sorted(resolved)


def test_resolved_logical_name_pattern() -> None:
    sig = WifiSignalIw("wlan0")
    assert sig.resolved_logical_name() == "wifi_wlan0_signal"
    ssid = WifiSsidNmcli("wlp3s0")
    assert ssid.resolved_logical_name() == "wifi_wlp3s0_ssid"


def _mock_proc(stdout: bytes, returncode: int) -> AsyncMock:
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.wait = AsyncMock(return_value=returncode)
    proc.kill = AsyncMock()
    proc.returncode = returncode
    return proc


async def test_signal_iw_parses_dbm_from_link_output() -> None:
    output = (
        b"Connected to aa:bb:cc:dd:ee:ff (on wlan0)\n"
        b"\tSSID: MyNet\n"
        b"\tfreq: 5180\n"
        b"\tsignal: -52 dBm\n"
        b"\ttx bitrate: 234.0 MBit/s\n"
    )
    proc = _mock_proc(output, 0)
    with patch(
        "system_sensors.sensors.wifi.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        reading = await WifiSignalIw("wlan0").collect()
    assert isinstance(reading, SensorReading)
    assert reading.unavailable is False
    assert reading.value == -52


async def test_signal_nmcli_filters_to_active_on_iface() -> None:
    output = b" :40:wlan0\n*:73:wlp3s0\n :22:wlp3s0\n"
    proc = _mock_proc(output, 0)
    with patch(
        "system_sensors.sensors.wifi.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        reading = await WifiSignalNmcli("wlp3s0").collect()
    assert reading.unavailable is False
    assert reading.value == 73


async def test_signal_proc_parses_third_column_dbm(tmp_path) -> None:
    sample = (
        "Inter-| sta-|   Quality        |   Discarded packets\n"
        " face | tus | link level noise |  nwid  crypt   frag  retry  misc\n"
        " wlan0: 0000   70.  -52.  -256        0      0      0      0      0\n"
    )
    fake = tmp_path / "wireless"
    fake.write_text(sample)
    with patch("system_sensors.sensors.wifi._PROC_NET_WIRELESS", fake):
        reading = await WifiSignalProc("wlan0").collect()
    assert reading.unavailable is False
    assert reading.value == -52


async def test_signal_proc_returns_unavailable_for_missing_iface(tmp_path) -> None:
    fake = tmp_path / "wireless"
    fake.write_text("Inter-|...\n face |...\n other: 0 60. -55. -256 0 0 0 0 0\n")
    with patch("system_sensors.sensors.wifi._PROC_NET_WIRELESS", fake):
        reading = await WifiSignalProc("wlan0").collect()
    assert reading.unavailable is True


async def test_ssid_iw_parses_ssid_line() -> None:
    output = b"Connected to aa:bb (on wlan0)\n\tSSID: CoffeeShop\n\tfreq: 5180\n"
    proc = _mock_proc(output, 0)
    with patch(
        "system_sensors.sensors.wifi.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        reading = await WifiSsidIw("wlan0").collect()
    assert reading.unavailable is False
    assert reading.value == "CoffeeShop"


async def test_ssid_nmcli_filters_active_yes() -> None:
    output = b"no:OtherNet:wlan0\nyes:HomeNet:wlp3s0\nno:Stale:wlp3s0\n"
    proc = _mock_proc(output, 0)
    with patch(
        "system_sensors.sensors.wifi.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        reading = await WifiSsidNmcli("wlp3s0").collect()
    assert reading.unavailable is False
    assert reading.value == "HomeNet"


async def test_ssid_iwgetid_uses_stdout() -> None:
    proc = _mock_proc(b"MyHomeWifi\n", 0)
    with patch(
        "system_sensors.sensors.wifi.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        reading = await WifiSsidIwgetid("wlan0").collect()
    assert reading.unavailable is False
    assert reading.value == "MyHomeWifi"


async def test_ssid_iwgetid_empty_is_unavailable() -> None:
    proc = _mock_proc(b"\n", 0)
    with patch(
        "system_sensors.sensors.wifi.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        reading = await WifiSsidIwgetid("wlan0").collect()
    assert reading.unavailable is True


async def test_subprocess_missing_binary_is_unavailable() -> None:
    with patch(
        "system_sensors.sensors.wifi.asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=FileNotFoundError("iw")),
    ):
        reading = await WifiSignalIw("wlan0").collect()
    assert reading.unavailable is True


@pytest.mark.skipif(
    not (WifiSignalIw.probe() or WifiSignalNmcli.probe() or WifiSignalProc.probe()),
    reason="no wifi signal variant available on this host",
)
async def test_wifi_signal_live_collect_returns_int_or_unavailable() -> None:
    for cls in (WifiSignalIw, WifiSignalNmcli, WifiSignalProc):
        if not cls.probe():
            continue
        for inst in cls.enumerate_instances():
            reading = await inst.collect()
            assert isinstance(reading, SensorReading)
            if not reading.unavailable:
                assert isinstance(reading.value, int)
