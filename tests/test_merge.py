"""Tests for `sensors_enabled.yaml` re-run merge semantics.

Every row of the merge truth table from PLAN.md is covered.
"""

from __future__ import annotations

from datetime import datetime, timezone

from system_sensors.config import SensorEntry, SensorsEnabled
from system_sensors.merge import MergeResult, merge_probe_with_existing


_NOW = datetime(2026, 5, 14, 17, 42, 0, tzinfo=timezone.utc)


def _entry(enabled: bool, available: bool) -> SensorEntry:
    return SensorEntry(enabled=enabled, available=available)


def _existing(**sensors: SensorEntry) -> SensorsEnabled:
    return SensorsEnabled(
        sensors=dict(sensors),
        last_probe_utc=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )


def test_first_install_no_existing() -> None:
    result = merge_probe_with_existing({"A"}, None, _NOW)
    assert isinstance(result, MergeResult)
    assert result.added == ["A"]
    assert result.removed == []
    assert result.new_state.sensors["A"] == _entry(True, True)
    assert result.new_state.last_probe_utc == _NOW


def test_new_sensor_added_with_enabled_and_available_true() -> None:
    result = merge_probe_with_existing({"A"}, _existing(), _NOW)
    assert result.added == ["A"]
    assert result.new_state.sensors["A"].enabled is True
    assert result.new_state.sensors["A"].available is True


def test_kept_available_unchanged() -> None:
    existing = _existing(A=_entry(True, True))
    result = merge_probe_with_existing({"A"}, existing, _NOW)
    assert result.kept == ["A"]
    assert result.added == []
    assert result.new_state.sensors["A"] == _entry(True, True)


def test_kept_available_user_disabled_preserved() -> None:
    existing = _existing(A=_entry(False, True))
    result = merge_probe_with_existing({"A"}, existing, _NOW)
    assert result.kept == ["A"]
    assert result.new_state.sensors["A"].enabled is False
    assert result.new_state.sensors["A"].available is True
    assert "A" in result.user_disabled


def test_became_unavailable_enabled_flag_preserved() -> None:
    existing = _existing(A=_entry(True, True))
    result = merge_probe_with_existing(set(), existing, _NOW)
    assert result.became_unavailable == ["A"]
    assert result.kept == []
    assert result.new_state.sensors["A"].enabled is True
    assert result.new_state.sensors["A"].available is False


def test_became_unavailable_user_disabled_preserved() -> None:
    existing = _existing(A=_entry(False, True))
    result = merge_probe_with_existing(set(), existing, _NOW)
    assert result.became_unavailable == ["A"]
    assert result.new_state.sensors["A"].enabled is False
    assert result.new_state.sensors["A"].available is False


def test_became_available_again_enabled_preserved() -> None:
    existing = _existing(A=_entry(True, False))
    result = merge_probe_with_existing({"A"}, existing, _NOW)
    assert result.became_available_again == ["A"]
    assert result.new_state.sensors["A"].enabled is True
    assert result.new_state.sensors["A"].available is True


def test_became_available_again_user_disabled_preserved() -> None:
    existing = _existing(A=_entry(False, False))
    result = merge_probe_with_existing({"A"}, existing, _NOW)
    assert result.became_available_again == ["A"]
    assert result.new_state.sensors["A"].enabled is False
    assert result.new_state.sensors["A"].available is True


def test_never_removed_when_still_unavailable() -> None:
    existing = _existing(A=_entry(True, False))
    result = merge_probe_with_existing(set(), existing, _NOW)
    assert "A" in result.new_state.sensors
    assert result.new_state.sensors["A"].available is False
    assert result.kept == ["A"]
    assert result.became_unavailable == []
    assert result.became_available_again == []


def test_removed_list_always_empty() -> None:
    for existing in (
        None,
        _existing(),
        _existing(A=_entry(True, True)),
        _existing(A=_entry(False, False)),
    ):
        result = merge_probe_with_existing(set(), existing, _NOW)
        assert result.removed == []


def test_now_utc_set_into_new_state() -> None:
    custom = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    result = merge_probe_with_existing({"A"}, None, custom)
    assert result.new_state.last_probe_utc == custom


def test_empty_existing_none_acts_like_empty_dict() -> None:
    none_result = merge_probe_with_existing({"A", "B"}, None, _NOW)
    empty_result = merge_probe_with_existing({"A", "B"}, _existing(), _NOW)
    assert none_result.added == empty_result.added
    assert none_result.new_state.sensors == empty_result.new_state.sensors


def test_mixed_scenario() -> None:
    existing = _existing(
        keep=_entry(True, True),
        user_off=_entry(False, True),
        gone=_entry(True, True),
        back=_entry(True, False),
    )
    probe = {"keep", "user_off", "back", "brand_new"}
    result = merge_probe_with_existing(probe, existing, _NOW)
    assert "brand_new" in result.added
    assert "gone" in result.became_unavailable
    assert "back" in result.became_available_again
    assert set(result.kept) == {"keep", "user_off"}
    assert result.new_state.sensors["user_off"].enabled is False
    assert result.new_state.sensors["gone"].available is False
    assert result.new_state.sensors["back"].available is True
