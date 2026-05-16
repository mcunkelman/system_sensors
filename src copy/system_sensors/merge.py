"""Re-run merge logic for `sensors_enabled.yaml`.

See `~/sensors/PLAN.md` section "Merge semantics" for the truth table this
implements. Pure functions with no I/O.

Truth table (probe_True column means "probed True this run"):

| Old state                             | Probe True | Probe False                  |
| ------------------------------------- | ---------- | ---------------------------- |
| Absent                                | added      | (not represented)            |
| enabled=True, available=True          | kept       | became_unavailable           |
| enabled=False, available=True         | kept       | became_unavailable           |
| enabled=True, available=False         | became_available_again | kept (still unavail) |
| enabled=False, available=False        | became_available_again | kept (still unavail) |

User `enabled` toggles are always preserved. Entries are never removed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from system_sensors.config import SensorEntry, SensorsEnabled


class MergeError(Exception):
    """Raised when merge inputs are inconsistent."""


@dataclass(frozen=True)
class MergeResult:
    """Result of merging probe output with an existing sensors_enabled state.

    Attributes:
        new_state: The merged SensorsEnabled ready to write.
        added: Logical names newly detected this run.
        removed: Always empty in v1; entries are never dropped.
        became_unavailable: Previously available, now not.
        became_available_again: Previously unavailable, now available.
        kept: Entries whose available state did not change this run.
        user_disabled: Names currently flagged enabled=False (informational).
    """

    new_state: SensorsEnabled
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    became_unavailable: list[str] = field(default_factory=list)
    became_available_again: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    user_disabled: list[str] = field(default_factory=list)


def merge_probe_with_existing(
    probed_sensor_names: set[str],
    existing: SensorsEnabled | None,
    now_utc: datetime,
) -> MergeResult:
    """Apply the merge rules from PLAN.md "Merge semantics".

    Rules:
    - Newly detected (in probe, not in existing): added with enabled=True,
      available=True.
    - Previously detected, still probes True: kept; enabled flag preserved;
      available=True.
    - Previously detected, now probes False: enabled flag preserved;
      available=False; goes on became_unavailable if it was previously
      available.
    - Previously unavailable, now probes True: available=True; enabled flag
      preserved; goes on became_available_again.
    - Never removed.
    """
    existing_sensors: dict[str, SensorEntry] = (
        dict(existing.sensors) if existing is not None else {}
    )

    new_sensors: dict[str, SensorEntry] = {}
    added: list[str] = []
    became_unavailable: list[str] = []
    became_available_again: list[str] = []
    kept: list[str] = []
    user_disabled: list[str] = []

    all_names = set(existing_sensors.keys()) | set(probed_sensor_names)
    for name in sorted(all_names):
        in_probe = name in probed_sensor_names
        prev = existing_sensors.get(name)
        if prev is None:
            new_entry = SensorEntry(enabled=True, available=True)
            new_sensors[name] = new_entry
            added.append(name)
        else:
            enabled = prev.enabled
            was_available = prev.available
            now_available = in_probe
            new_sensors[name] = SensorEntry(enabled=enabled, available=now_available)
            if was_available and not now_available:
                became_unavailable.append(name)
            elif not was_available and now_available:
                became_available_again.append(name)
            else:
                kept.append(name)

        if not new_sensors[name].enabled:
            user_disabled.append(name)

    new_state = SensorsEnabled(sensors=new_sensors, last_probe_utc=now_utc)
    return MergeResult(
        new_state=new_state,
        added=added,
        removed=[],
        became_unavailable=became_unavailable,
        became_available_again=became_available_again,
        kept=kept,
        user_disabled=user_disabled,
    )


def diff_summary(result: MergeResult) -> dict[str, Any]:
    """Render a merge result as a JSON-friendly dict for logging / dry-run output."""
    return {
        "added": list(result.added),
        "became_unavailable": list(result.became_unavailable),
        "became_available_again": list(result.became_available_again),
        "kept": list(result.kept),
        "removed": list(result.removed),
        "user_disabled": list(result.user_disabled),
    }
