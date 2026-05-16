"""Network sensors.

Provides:
- `NetTxTotal`, `NetRxTotal` — bytes-per-second deltas summed across all
  non-loopback interfaces (aggregate view).
- `NetTxIface`, `NetRxIface` — per-interface variants. `enumerate_instances()`
  discovers every non-virtual, non-loopback interface at install time and
  returns one sensor instance per interface. Resolved logical names follow
  ``net_tx_<iface>`` / ``net_rx_<iface>`` (e.g. ``net_tx_eth0``).

Virtual interface prefixes filtered during enumeration: loopback (``lo``),
container/tunnel (``veth``, ``docker``, ``virbr``, ``br-``, ``tun``, ``tap``),
bonding helpers (``dummy``, ``bond``, ``team``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import psutil

from system_sensors.sensors.base import Sensor, SensorReading


def _sum_non_loopback(direction: str) -> int:
    """Sum a counter field across non-loopback interfaces.

    `direction` is `bytes_sent` or `bytes_recv`.
    """
    counters = psutil.net_io_counters(pernic=True)
    total = 0
    for iface, stats in counters.items():
        if iface == "lo" or iface.startswith("lo:"):
            continue
        total += int(getattr(stats, direction))
    return total


class _NetCounterBase(Sensor):
    """Shared delta-tracking for cumulative byte counters.

    State (last bytes seen, last timestamp) lives on the instance. The first
    `collect()` is a warmup that records baseline and returns 0. Counter
    wraparound (current < last) is handled by resetting state.
    """

    unit: ClassVar[str | None] = "B/s"
    device_class: ClassVar[str | None] = "data_rate"
    state_class: ClassVar[str | None] = "measurement"
    priority: ClassVar[int] = 100

    _DIRECTION: ClassVar[str] = ""

    def __init__(self) -> None:
        self._last_bytes: int | None = None
        self._last_timestamp: datetime | None = None

    @classmethod
    def probe(cls) -> bool:
        return hasattr(psutil, "net_io_counters")

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            current = _sum_non_loopback(type(self)._DIRECTION)
            if self._last_bytes is None or self._last_timestamp is None:
                self._last_bytes = current
                self._last_timestamp = now
                return SensorReading(value=0.0, timestamp=now)
            if current < self._last_bytes:
                self._last_bytes = current
                self._last_timestamp = now
                return SensorReading(value=0.0, timestamp=now)
            elapsed = (now - self._last_timestamp).total_seconds()
            if elapsed <= 0:
                return SensorReading(value=0.0, timestamp=now)
            rate = (current - self._last_bytes) / elapsed
            self._last_bytes = current
            self._last_timestamp = now
            return SensorReading(value=round(rate, 2), timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class NetTxTotal(_NetCounterBase):
    """Aggregate transmit throughput across non-loopback interfaces."""

    logical_name: ClassVar[str] = "net_tx_total"
    icon: ClassVar[str | None] = "mdi:upload"
    _DIRECTION: ClassVar[str] = "bytes_sent"


class NetRxTotal(_NetCounterBase):
    """Aggregate receive throughput across non-loopback interfaces."""

    logical_name: ClassVar[str] = "net_rx_total"
    icon: ClassVar[str | None] = "mdi:download"
    _DIRECTION: ClassVar[str] = "bytes_recv"


# ---------------------------------------------------------------------------
# Per-interface helpers
# ---------------------------------------------------------------------------

# Interface name prefixes that indicate virtual / container / loopback
# interfaces not worth monitoring individually.
_IGNORED_IFACE_PREFIXES: tuple[str, ...] = (
    "lo",
    "veth",
    "docker",
    "virbr",
    "br-",
    "tun",
    "tap",
    "dummy",
    "bond",
    "team",
)


def _real_interfaces() -> list[str]:
    """Return names of real (non-virtual, non-loopback) network interfaces.

    Uses psutil.net_if_stats() so only interfaces the kernel knows about are
    returned. Sorted for deterministic enumeration order.
    """
    try:
        return sorted(
            iface
            for iface in psutil.net_if_stats()
            if not any(iface.startswith(p) for p in _IGNORED_IFACE_PREFIXES)
        )
    except Exception:
        return []


def _read_iface_counter(iface: str, direction: str) -> int:
    """Read a single counter field for one named interface.

    `direction` is ``bytes_sent`` or ``bytes_recv``.
    Raises KeyError if the interface is not found (e.g. unplugged after
    install); callers treat that as a wraparound / reset.
    """
    counters = psutil.net_io_counters(pernic=True)
    stats = counters[iface]
    return int(getattr(stats, direction))


class _NetIfaceCounterBase(Sensor):
    """Delta-tracking base for per-interface byte counters.

    Mirrors `_NetCounterBase` but reads from a single named interface.
    State (last bytes, last timestamp) lives on the instance. First collect()
    is a warmup returning 0. Counter wraparound and missing interfaces are
    handled by resetting state.
    """

    unit: ClassVar[str | None] = "B/s"
    device_class: ClassVar[str | None] = "data_rate"
    state_class: ClassVar[str | None] = "measurement"
    priority: ClassVar[int] = 100

    _DIRECTION: ClassVar[str] = ""
    _SUFFIX: ClassVar[str] = ""

    def __init__(self, interface: str) -> None:
        self._interface = interface
        self._last_bytes: int | None = None
        self._last_timestamp: datetime | None = None

    @classmethod
    def probe(cls) -> bool:
        return hasattr(psutil, "net_io_counters") and bool(_real_interfaces())

    @classmethod
    def enumerate_instances(cls) -> list["Sensor"]:
        return [cls(iface) for iface in _real_interfaces()]

    def resolved_logical_name(self) -> str:
        return f"{type(self)._SUFFIX}_{self._interface}"

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            current = _read_iface_counter(self._interface, type(self)._DIRECTION)
        except (KeyError, Exception):
            # Interface disappeared or psutil error — reset state, report unavailable
            self._last_bytes = None
            self._last_timestamp = None
            return SensorReading(value=None, timestamp=now, unavailable=True)

        if self._last_bytes is None or self._last_timestamp is None:
            self._last_bytes = current
            self._last_timestamp = now
            return SensorReading(value=0.0, timestamp=now)

        if current < self._last_bytes:
            # Counter wraparound — reset
            self._last_bytes = current
            self._last_timestamp = now
            return SensorReading(value=0.0, timestamp=now)

        elapsed = (now - self._last_timestamp).total_seconds()
        if elapsed <= 0:
            return SensorReading(value=0.0, timestamp=now)

        rate = (current - self._last_bytes) / elapsed
        self._last_bytes = current
        self._last_timestamp = now
        return SensorReading(value=round(rate, 2), timestamp=now)


class NetTxIface(_NetIfaceCounterBase):
    """Per-interface transmit throughput (B/s).

    One instance per real interface. Resolved name: ``net_tx_<iface>``.
    """

    logical_name: ClassVar[str] = "net_tx_iface"
    icon: ClassVar[str | None] = "mdi:upload-network"
    _DIRECTION: ClassVar[str] = "bytes_sent"
    _SUFFIX: ClassVar[str] = "net_tx"


class NetRxIface(_NetIfaceCounterBase):
    """Per-interface receive throughput (B/s).

    One instance per real interface. Resolved name: ``net_rx_<iface>``.
    """

    logical_name: ClassVar[str] = "net_rx_iface"
    icon: ClassVar[str | None] = "mdi:download-network"
    _DIRECTION: ClassVar[str] = "bytes_recv"
    _SUFFIX: ClassVar[str] = "net_rx"
