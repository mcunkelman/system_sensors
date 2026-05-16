"""Wi-Fi sensors (signal strength, SSID).

Multi-variant + multi-instance:
- Signal variants: `WifiSignalIw` (iw, dBm) > `WifiSignalNmcli` (nmcli, %) >
  `WifiSignalProc` (/proc/net/wireless, dBm).
- SSID variants: `WifiSsidIw` > `WifiSsidNmcli` > `WifiSsidIwgetid`.

For each variant, `enumerate_instances()` yields one instance per wireless
network interface discovered via sysfs (`/sys/class/net/<iface>/wireless`).
Resolved logical names are `f"wifi_{iface}_signal"` and `f"wifi_{iface}_ssid"`.

System dependencies (any one of, per host):
- `iw` (modern Linux nl80211 tool)
- `nmcli` (NetworkManager CLI)
- `iwgetid` (wireless-tools; SSID only)
- `/proc/net/wireless` (kernel-provided, always available on Linux with wireless)

Signal strength unit varies by variant: iw and /proc report dBm, nmcli reports
a 0-100 percentage.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from system_sensors.sensors.base import Sensor, SensorReading

_log = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT_SECONDS: float = 25.0
_SYS_CLASS_NET = Path("/sys/class/net")
_PROC_NET_WIRELESS = Path("/proc/net/wireless")


def _find_wireless_interfaces() -> list[str]:
    """Return interface names with a wireless sysfs subdirectory, sorted."""
    if not _SYS_CLASS_NET.exists():
        return []
    try:
        return sorted(
            iface.name
            for iface in _SYS_CLASS_NET.iterdir()
            if (iface / "wireless").is_dir()
        )
    except OSError:
        return []


async def _run(argv: list[str]) -> tuple[int | None, bytes]:
    """Run a subprocess with timeout, capturing stdout. stderr is discarded."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return None, b""
    except Exception:
        return None, b""
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
        return None, b""
    return proc.returncode, stdout or b""


_IW_SIGNAL_RE = re.compile(r"^\s*signal:\s*(-?\d+)\s*dBm", re.MULTILINE)
_IW_SSID_RE = re.compile(r"^\s*SSID:\s*(.+?)\s*$", re.MULTILINE)


class _WifiInstanceMixin:
    """Shared per-iface state and naming for both signal and SSID variants."""

    _suffix: ClassVar[str] = ""

    def __init__(self, interface: str) -> None:
        self._interface = interface

    @classmethod
    def enumerate_instances(cls) -> list[Sensor]:
        return [cls(iface) for iface in _find_wireless_interfaces()]  # type: ignore[call-arg]

    def resolved_logical_name(self) -> str:
        return f"wifi_{self._interface}_{type(self)._suffix}"


class WifiSignalIw(_WifiInstanceMixin, Sensor):
    """Wi-Fi RSSI via `iw dev <iface> link` (dBm)."""

    logical_name: ClassVar[str] = "wifi_signal"
    unit: ClassVar[str | None] = "dBm"
    device_class: ClassVar[str | None] = "signal_strength"
    state_class: ClassVar[str | None] = "measurement"
    icon: ClassVar[str | None] = "mdi:wifi"
    priority: ClassVar[int] = 300
    _suffix: ClassVar[str] = "signal"

    @classmethod
    def probe(cls) -> bool:
        return (
            shutil.which("iw") is not None
            and bool(_find_wireless_interfaces())
        )

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            rc, stdout = await _run(["iw", "dev", self._interface, "link"])
            if rc != 0 or not stdout:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            match = _IW_SIGNAL_RE.search(stdout.decode("utf-8", errors="replace"))
            if not match:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            return SensorReading(value=int(match.group(1)), timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class WifiSignalNmcli(_WifiInstanceMixin, Sensor):
    """Wi-Fi signal via `nmcli` (0-100 %)."""

    logical_name: ClassVar[str] = "wifi_signal"
    unit: ClassVar[str | None] = "%"
    device_class: ClassVar[str | None] = "signal_strength"
    state_class: ClassVar[str | None] = "measurement"
    icon: ClassVar[str | None] = "mdi:wifi"
    priority: ClassVar[int] = 200
    _suffix: ClassVar[str] = "signal"

    @classmethod
    def probe(cls) -> bool:
        return (
            shutil.which("nmcli") is not None
            and bool(_find_wireless_interfaces())
        )

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            rc, stdout = await _run(
                ["nmcli", "-t", "-f", "IN-USE,SIGNAL,DEVICE", "dev", "wifi"]
            )
            if rc != 0 or not stdout:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            for raw in stdout.decode("utf-8", errors="replace").splitlines():
                # Format: `<in-use>:<signal>:<device>`; in-use is "*" for active.
                # nmcli escapes embedded colons as `\:` but DEVICE/SIGNAL are safe.
                parts = raw.split(":")
                if len(parts) < 3:
                    continue
                in_use, signal, device = parts[0], parts[1], parts[2]
                if in_use.strip() != "*":
                    continue
                if device.strip() != self._interface:
                    continue
                return SensorReading(value=int(signal.strip()), timestamp=now)
            return SensorReading(value=None, timestamp=now, unavailable=True)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class WifiSignalProc(_WifiInstanceMixin, Sensor):
    """Wi-Fi signal from `/proc/net/wireless` (column 3 = level dBm)."""

    logical_name: ClassVar[str] = "wifi_signal"
    unit: ClassVar[str | None] = "dBm"
    device_class: ClassVar[str | None] = "signal_strength"
    state_class: ClassVar[str | None] = "measurement"
    icon: ClassVar[str | None] = "mdi:wifi"
    priority: ClassVar[int] = 100
    _suffix: ClassVar[str] = "signal"

    @classmethod
    def probe(cls) -> bool:
        return _PROC_NET_WIRELESS.exists() and bool(_find_wireless_interfaces())

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            content = _PROC_NET_WIRELESS.read_text(encoding="utf-8", errors="replace")
            prefix = f"{self._interface}:"
            for raw in content.splitlines():
                line = raw.strip()
                if not line.startswith(prefix):
                    continue
                # Columns after the iface label: status, link, level, noise, ...
                # `level` (dBm) is index 3 once we drop the iface token.
                rest = line[len(prefix):].split()
                if len(rest) < 4:
                    return SensorReading(value=None, timestamp=now, unavailable=True)
                # Values may carry a trailing `.` (e.g. `-52.`); strip it.
                level = rest[2].rstrip(".")
                return SensorReading(value=int(float(level)), timestamp=now)
            return SensorReading(value=None, timestamp=now, unavailable=True)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class WifiSsidIw(_WifiInstanceMixin, Sensor):
    """Connected SSID via `iw dev <iface> link`."""

    logical_name: ClassVar[str] = "wifi_ssid"
    icon: ClassVar[str | None] = "mdi:wifi"
    priority: ClassVar[int] = 300
    _suffix: ClassVar[str] = "ssid"

    @classmethod
    def probe(cls) -> bool:
        return (
            shutil.which("iw") is not None
            and bool(_find_wireless_interfaces())
        )

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            rc, stdout = await _run(["iw", "dev", self._interface, "link"])
            if rc != 0 or not stdout:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            match = _IW_SSID_RE.search(stdout.decode("utf-8", errors="replace"))
            if not match:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            ssid = match.group(1).strip()
            if not ssid:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            return SensorReading(value=ssid, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class WifiSsidNmcli(_WifiInstanceMixin, Sensor):
    """Connected SSID via `nmcli dev wifi`, filtered to the active network on `<iface>`."""

    logical_name: ClassVar[str] = "wifi_ssid"
    icon: ClassVar[str | None] = "mdi:wifi"
    priority: ClassVar[int] = 200
    _suffix: ClassVar[str] = "ssid"

    @classmethod
    def probe(cls) -> bool:
        return (
            shutil.which("nmcli") is not None
            and bool(_find_wireless_interfaces())
        )

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            rc, stdout = await _run(
                ["nmcli", "-t", "-f", "ACTIVE,SSID,DEVICE", "dev", "wifi"]
            )
            if rc != 0 or not stdout:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            for raw in stdout.decode("utf-8", errors="replace").splitlines():
                parts = raw.split(":")
                if len(parts) < 3:
                    continue
                active, ssid, device = parts[0], parts[1], parts[2]
                if active.strip().lower() != "yes":
                    continue
                if device.strip() != self._interface:
                    continue
                ssid = ssid.strip()
                if not ssid:
                    return SensorReading(value=None, timestamp=now, unavailable=True)
                return SensorReading(value=ssid, timestamp=now)
            return SensorReading(value=None, timestamp=now, unavailable=True)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class WifiSsidIwgetid(_WifiInstanceMixin, Sensor):
    """Connected SSID via `iwgetid -r <iface>`."""

    logical_name: ClassVar[str] = "wifi_ssid"
    icon: ClassVar[str | None] = "mdi:wifi"
    priority: ClassVar[int] = 100
    _suffix: ClassVar[str] = "ssid"

    @classmethod
    def probe(cls) -> bool:
        return (
            shutil.which("iwgetid") is not None
            and bool(_find_wireless_interfaces())
        )

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            rc, stdout = await _run(["iwgetid", "-r", self._interface])
            if rc != 0:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            ssid = stdout.decode("utf-8", errors="replace").strip()
            if not ssid:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            return SensorReading(value=ssid, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)
