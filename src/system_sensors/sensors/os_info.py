"""OS metadata sensors.

Provides `Hostname`, `HostIp`, `HostArch`, `LastBoot`, `HostOs`.

`HostOs` is Linux-only: it requires `/etc/os-release` to be present. The
parser prefers `platform.freedesktop_os_release()` (stdlib, Python 3.10+) and
falls back to a manual reader for older interpreters.
"""

from __future__ import annotations

import ipaddress
import logging
import platform
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

import psutil

from system_sensors.sensors.base import Sensor, SensorReading

_log = logging.getLogger(__name__)

_OS_RELEASE_PATH = Path("/etc/os-release")


class Hostname(Sensor):
    """Host's network name from `socket.gethostname`."""

    logical_name: ClassVar[str] = "hostname"
    icon: ClassVar[str | None] = "mdi:server"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        return True

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            return SensorReading(value=socket.gethostname(), timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class HostIp(Sensor):
    """Primary non-loopback IPv4 address.

    Iterates `psutil.net_if_addrs()` and returns the first IPv4 on an up
    interface that is not loopback and not link-local. Avoids
    `socket.gethostbyname` which can return `127.0.1.1` on some Linux configs.
    """

    logical_name: ClassVar[str] = "host_ip"
    icon: ClassVar[str | None] = "mdi:ip-network"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        return hasattr(psutil, "net_if_addrs")

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            stats = psutil.net_if_stats()
            for iface, addrs in psutil.net_if_addrs().items():
                iface_stats = stats.get(iface)
                if iface_stats is None or not iface_stats.isup:
                    continue
                for addr in addrs:
                    if addr.family != socket.AF_INET:
                        continue
                    try:
                        ip = ipaddress.IPv4Address(addr.address)
                    except (ipaddress.AddressValueError, ValueError):
                        continue
                    if ip.is_loopback or ip.is_link_local:
                        continue
                    return SensorReading(value=str(ip), timestamp=now)
            return SensorReading(value=None, timestamp=now, unavailable=True)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class HostArch(Sensor):
    """Machine architecture string from `platform.machine`."""

    logical_name: ClassVar[str] = "host_arch"
    icon: ClassVar[str | None] = "mdi:chip"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        return True

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            value = platform.machine() or None
            if not value:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            return SensorReading(value=value, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


def _parse_os_release_file(path: Path) -> dict[str, str]:
    """Best-effort parse of `/etc/os-release`. Returns an empty dict on any I/O error."""
    out: dict[str, str] = {}
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key.strip()] = value
    return out


class HostOs(Sensor):
    """Human-readable host OS string from `/etc/os-release`.

    Prefers `PRETTY_NAME`; falls back to `f"{NAME} {VERSION_ID}"` when only the
    constituent fields are present.
    """

    logical_name: ClassVar[str] = "host_os"
    icon: ClassVar[str | None] = "mdi:linux"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        return _OS_RELEASE_PATH.exists()

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            data: dict[str, str] = {}
            stdlib_reader = getattr(platform, "freedesktop_os_release", None)
            if stdlib_reader is not None:
                try:
                    data = dict(stdlib_reader())
                except OSError:
                    data = {}
            if not data:
                data = _parse_os_release_file(_OS_RELEASE_PATH)
            if not data:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            pretty = data.get("PRETTY_NAME")
            if pretty:
                return SensorReading(value=pretty, timestamp=now)
            name = data.get("NAME")
            version = data.get("VERSION_ID")
            if name and version:
                return SensorReading(value=f"{name} {version}", timestamp=now)
            if name:
                return SensorReading(value=name, timestamp=now)
            return SensorReading(value=None, timestamp=now, unavailable=True)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class LastBoot(Sensor):
    """Host boot time as a tz-aware UTC ISO 8601 string."""

    logical_name: ClassVar[str] = "last_boot"
    device_class: ClassVar[str | None] = "timestamp"
    icon: ClassVar[str | None] = "mdi:clock"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        return hasattr(psutil, "boot_time")

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            boot_dt = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)
            return SensorReading(value=boot_dt.isoformat(), timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)
