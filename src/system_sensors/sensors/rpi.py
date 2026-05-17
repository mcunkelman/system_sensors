"""Raspberry Pi specific sensors.

Provides `RpiPowerStatus` — wraps the `rpi_bad_power` optional dependency,
which reads the GPIO firmware throttle register to detect under-voltage and
related power conditions. The dependency is not pip-installed by default;
the probe returns False when the import or the device-tree model check fails.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from system_sensors.sensors.base import Sensor, SensorReading

_log = logging.getLogger(__name__)

_DEVICE_TREE_MODEL = Path("/proc/device-tree/model")


def _is_raspberry_pi() -> bool:
    """True iff `/proc/device-tree/model` mentions Raspberry Pi."""
    if not _DEVICE_TREE_MODEL.exists():
        return False
    try:
        content = _DEVICE_TREE_MODEL.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "raspberry pi" in content.lower()


def _rpi_bad_power_available() -> bool:
    """True iff the optional `rpi_bad_power` module imports cleanly."""
    try:
        import rpi_bad_power  # noqa: F401
    except Exception:
        return False
    return True


class RpiPowerStatus(Sensor):
    """Raspberry Pi power / throttle status via `rpi_bad_power`.

    Probe requires both the optional pip dependency `rpi_bad_power` to import
    and the host to be a Raspberry Pi (per `/proc/device-tree/model`). The
    collected value is `"OK"` when no flags are active, otherwise a comma-
    separated list of active flag names (e.g. `"under_voltage,throttled"`).
    """

    logical_name: ClassVar[str] = "power_status"
    icon: ClassVar[str | None] = "mdi:flash"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        return _rpi_bad_power_available() and _is_raspberry_pi()

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            import rpi_bad_power

            instance = None
            new_fn = getattr(rpi_bad_power, "new_under_voltage", None)
            if callable(new_fn):
                instance = new_fn()
            if instance is None:
                cls_ref = getattr(rpi_bad_power, "RPiBadPower", None)
                if cls_ref is not None and hasattr(cls_ref, "new"):
                    instance = cls_ref.new()
            if instance is None:
                return SensorReading(value=None, timestamp=now, unavailable=True)

            flags: list[str] = []
            try:
                if hasattr(instance, "under_voltage") and instance.under_voltage():
                    flags.append("under_voltage")
            except Exception:
                pass
            try:
                if hasattr(instance, "frequency_capped") and instance.frequency_capped():
                    flags.append("frequency_capped")
            except Exception:
                pass
            try:
                if hasattr(instance, "throttled") and instance.throttled():
                    flags.append("throttled")
            except Exception:
                pass

            # Fallback: if the instance only exposes `.get()` (older API), use
            # that as a single under-voltage boolean.
            if not flags and hasattr(instance, "get"):
                try:
                    if instance.get():
                        flags.append("under_voltage")
                except Exception:
                    return SensorReading(value=None, timestamp=now, unavailable=True)

            value = "OK" if not flags else ",".join(flags)
            return SensorReading(value=value, timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)
