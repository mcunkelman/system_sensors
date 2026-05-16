"""Shared sysfs helpers for DRM-backed GPU sensors (AMD + Intel).

Both `amdgpu` and `i915` / `xe` expose temperature, fan, and power telemetry
through the kernel hwmon interface attached to each DRM card:

    /sys/class/drm/card<N>/device/uevent          -- contains DRIVER=<name>
    /sys/class/drm/card<N>/device/hwmon/hwmon<M>/ -- the hwmon directory

This module defines no `Sensor` subclasses; `gpu_amd.py` and `gpu_intel.py`
import the helpers to enumerate cards by driver and to locate the hwmon dir.
The leading underscore in the module name keeps the registry import walk
finding it via `pkgutil.iter_modules` while making clear it has no public
sensor classes.
"""

from __future__ import annotations

from pathlib import Path

_DRM_BASE = Path("/sys/class/drm")


def _read_uevent_driver(card_dir: Path) -> str | None:
    """Return DRIVER value from <card>/device/uevent, or None on error/missing."""
    uevent = card_dir / "device" / "uevent"
    if not uevent.is_file():
        return None
    try:
        text = uevent.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for raw in text.splitlines():
        if raw.startswith("DRIVER="):
            return raw[len("DRIVER="):].strip()
    return None


def _card_hwmon_dir(card_dir: Path) -> Path | None:
    """Return the hwmon subdir for a DRM card if at least one exists, else None.

    Path: ``<card>/device/hwmon/hwmon<N>/``. Some cards have zero, most one. If
    multiple are present (rare — happens when more than one hwmon class device
    is attached to the same PCI device) the lowest-numbered directory wins for
    stability across boots.
    """
    hw_root = card_dir / "device" / "hwmon"
    if not hw_root.is_dir():
        return None
    try:
        candidates = sorted(
            p for p in hw_root.iterdir()
            if p.is_dir() and p.name.startswith("hwmon")
        )
    except OSError:
        return None
    return candidates[0] if candidates else None


def enumerate_drm_cards_for_driver_with_file(
    driver_names: tuple[str, ...],
    filename: str,
) -> list[tuple[int, Path]]:
    """List `(card_index, hwmon_dir)` for cards whose driver matches AND whose
    hwmon dir contains ``filename``.

    Cards that match the driver but lack the requested file are silently
    filtered: callers want one instance per available metric, not perpetually-
    unavailable sensors. ``card_index`` is the integer N from ``card<N>`` so
    callers can compose resolved logical names like ``gpu_amd_<N>_temp``.

    Args:
        driver_names: Drivers to accept (e.g. ``("amdgpu",)`` or ``("i915", "xe")``).
        filename: hwmon attribute file required for this metric (e.g.
            ``"temp1_input"``, ``"power1_average"``).

    Returns:
        Pairs sorted by card index. Empty list if /sys/class/drm is absent
        (non-Linux hosts) or no card qualifies.
    """
    out: list[tuple[int, Path]] = []
    if not _DRM_BASE.exists():
        return out
    try:
        cards = sorted(
            p for p in _DRM_BASE.iterdir()
            if p.is_dir()
            and p.name.startswith("card")
            and p.name[4:].isdigit()
        )
    except OSError:
        return out
    for card in cards:
        driver = _read_uevent_driver(card)
        if driver not in driver_names:
            continue
        hwmon = _card_hwmon_dir(card)
        if hwmon is None:
            continue
        if not (hwmon / filename).is_file():
            continue
        try:
            index = int(card.name[4:])
        except ValueError:
            continue
        out.append((index, hwmon))
    return out
