"""Tests for package-manager update sensors."""

from __future__ import annotations

import shutil
from unittest.mock import AsyncMock, patch

import pytest

from system_sensors.sensors.base import SensorReading
from system_sensors.sensors.os_updates import (
    AptUpdates,
    DnfUpdates,
    PacmanUpdates,
)


def test_apt_probe_returns_bool() -> None:
    assert isinstance(AptUpdates.probe(), bool)


@pytest.mark.skipif(not AptUpdates.probe(), reason="python3-apt not available on this host")
async def test_apt_collect_returns_int_when_probe_true() -> None:
    reading = await AptUpdates().collect()
    assert isinstance(reading, SensorReading)
    assert reading.unavailable is False
    assert isinstance(reading.value, int)
    assert reading.value >= 0


def test_dnf_probe_returns_bool() -> None:
    assert isinstance(DnfUpdates.probe(), bool)


def test_pacman_probe_returns_bool() -> None:
    assert isinstance(PacmanUpdates.probe(), bool)


def test_dnf_probe_matches_dnf_on_path() -> None:
    assert DnfUpdates.probe() is (shutil.which("dnf") is not None)


def test_pacman_probe_matches_pacman_and_checkupdates_on_path() -> None:
    expected = shutil.which("pacman") is not None and shutil.which("checkupdates") is not None
    assert PacmanUpdates.probe() is expected


def _mock_proc(stdout: bytes, returncode: int) -> AsyncMock:
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.wait = AsyncMock(return_value=returncode)
    proc.kill = AsyncMock()
    proc.returncode = returncode
    return proc


async def test_dnf_parses_exit_100_as_line_count() -> None:
    stdout = b"pkg-a x86_64 1.0 repo\npkg-b x86_64 2.0 repo\n\n"
    proc = _mock_proc(stdout, 100)
    with patch(
        "system_sensors.sensors.os_updates.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        reading = await DnfUpdates().collect()
    assert reading.unavailable is False
    assert reading.value == 2


async def test_dnf_exit_zero_returns_zero_updates() -> None:
    proc = _mock_proc(b"", 0)
    with patch(
        "system_sensors.sensors.os_updates.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        reading = await DnfUpdates().collect()
    assert reading.unavailable is False
    assert reading.value == 0


async def test_dnf_other_exit_is_unavailable() -> None:
    proc = _mock_proc(b"error\n", 1)
    with patch(
        "system_sensors.sensors.os_updates.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        reading = await DnfUpdates().collect()
    assert reading.unavailable is True


async def test_pacman_parses_line_count() -> None:
    stdout = b"pkg-a 1.0-1 -> 1.1-1\npkg-b 2.0-1 -> 2.1-1\npkg-c 3.0-1 -> 3.0-2\n"
    proc = _mock_proc(stdout, 0)
    with patch(
        "system_sensors.sensors.os_updates.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        reading = await PacmanUpdates().collect()
    assert reading.unavailable is False
    assert reading.value == 3


async def test_pacman_empty_stdout_zero_updates() -> None:
    proc = _mock_proc(b"", 2)
    with patch(
        "system_sensors.sensors.os_updates.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        reading = await PacmanUpdates().collect()
    assert reading.unavailable is False
    assert reading.value == 0


async def test_apt_collect_unavailable_when_apt_missing() -> None:
    # If probe failed (apt isn't installed) collect must not crash.
    if AptUpdates.probe():
        pytest.skip("apt is available; can't test missing-apt branch here")
    reading = await AptUpdates().collect()
    assert reading.unavailable is True
