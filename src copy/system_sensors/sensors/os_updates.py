"""Package-manager available-update counters.

Provides `AptUpdates`, `DnfUpdates`, `PacmanUpdates`. All three share the
logical name `updates`; the registry's priority + probe machinery picks the
single variant whose package manager is present.

System dependencies:
- `AptUpdates` requires the system-installed `python3-apt` package (not
  pip-installable). The probe imports `apt` and instantiates a cache; failure
  to do either disables the variant.
- `DnfUpdates` requires the `dnf` binary on PATH (Fedora/RHEL/CentOS family).
- `PacmanUpdates` requires both `pacman` (Arch) and `checkupdates` from the
  `pacman-contrib` package. `checkupdates` is the safe, no-root option that
  doesn't sync the live database.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from typing import ClassVar

from system_sensors.sensors.base import Sensor, SensorReading

_log = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT_SECONDS: float = 25.0


class AptUpdates(Sensor):
    """Number of upgradable apt packages."""

    logical_name: ClassVar[str] = "updates"
    icon: ClassVar[str | None] = "mdi:package-up"
    state_class: ClassVar[str | None] = "measurement"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        try:
            import apt  # type: ignore[import-not-found]
        except Exception:
            return False
        try:
            apt.Cache()
        except Exception:
            return False
        return True

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            import apt  # type: ignore[import-not-found]

            cache = apt.Cache()
            count = sum(1 for pkg in cache if pkg.is_upgradable)
            return SensorReading(value=int(count), timestamp=now)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


async def _run_subprocess(
    argv: list[str],
) -> tuple[int | None, bytes]:
    """Run `argv`, returning `(returncode, stdout)` or `(None, b"")` on timeout/error.

    stdout is captured; stderr is discarded. Times out at the module-wide limit
    and kills the child on overrun.
    """
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


def _count_nonempty_lines(stdout: bytes) -> int:
    text = stdout.decode("utf-8", errors="replace")
    return sum(1 for line in text.splitlines() if line.strip())


class DnfUpdates(Sensor):
    """Number of available dnf updates.

    `dnf check-update` exits 100 when updates are available, 0 when none.
    """

    logical_name: ClassVar[str] = "updates"
    icon: ClassVar[str | None] = "mdi:package-up"
    state_class: ClassVar[str | None] = "measurement"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        return shutil.which("dnf") is not None

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            rc, stdout = await _run_subprocess(["dnf", "check-update", "--quiet"])
            if rc == 0:
                return SensorReading(value=0, timestamp=now)
            if rc == 100:
                return SensorReading(value=_count_nonempty_lines(stdout), timestamp=now)
            return SensorReading(value=None, timestamp=now, unavailable=True)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)


class PacmanUpdates(Sensor):
    """Number of pending pacman updates via `checkupdates`."""

    logical_name: ClassVar[str] = "updates"
    icon: ClassVar[str | None] = "mdi:package-up"
    state_class: ClassVar[str | None] = "measurement"
    priority: ClassVar[int] = 100

    @classmethod
    def probe(cls) -> bool:
        return (
            shutil.which("pacman") is not None
            and shutil.which("checkupdates") is not None
        )

    async def collect(self) -> SensorReading:
        now = datetime.now(timezone.utc)
        try:
            rc, stdout = await _run_subprocess(["checkupdates"])
            if rc is None:
                return SensorReading(value=None, timestamp=now, unavailable=True)
            # `checkupdates` exits non-zero when no updates AND when erroring;
            # the man page is loose, so treat empty stdout as "0 updates" and
            # only mark unavailable when rc indicates a real failure (>1).
            if rc in (0, 1, 2):
                return SensorReading(
                    value=_count_nonempty_lines(stdout), timestamp=now
                )
            return SensorReading(value=None, timestamp=now, unavailable=True)
        except Exception:
            return SensorReading(value=None, timestamp=now, unavailable=True)
