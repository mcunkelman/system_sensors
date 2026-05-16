"""Tests for the system_sensors installer CLI."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from system_sensors import installer
from system_sensors.config import (
    SensorEntry,
    SensorsEnabled,
    Settings,
    load_sensors_enabled,
    load_settings,
    save_sensors_enabled,
    save_settings,
    sensors_enabled_path,
    settings_path,
)
from system_sensors.sensors.base import Sensor, SensorReading


class _FakeSensor(Sensor):
    """Minimal Sensor used to feed canned probe outputs into the installer."""

    logical_name = "fake_sensor"

    def __init__(self, name: str) -> None:
        self._name = name

    @classmethod
    def probe(cls) -> bool:
        return True

    def resolved_logical_name(self) -> str:
        return self._name

    async def collect(self) -> SensorReading:  # pragma: no cover - not exercised
        from datetime import datetime, timezone

        return SensorReading(value=0, timestamp=datetime.now(timezone.utc))


def _patch_probe(monkeypatch: pytest.MonkeyPatch, names: list[str]) -> None:
    """Patch the probe pipeline so _probe_host yields canned sensor names."""
    monkeypatch.setattr(installer, "discover_sensors", lambda: [])
    monkeypatch.setattr(installer, "probe_all", lambda _classes: {})
    monkeypatch.setattr(installer, "select_variants", lambda _probed: {})
    monkeypatch.setattr(
        installer,
        "instantiate_active_sensors",
        lambda _selected: [_FakeSensor(n) for n in names],
    )


def _baseline_settings() -> Settings:
    return Settings(
        mqtt_hostname="broker.local",
        mqtt_port=1883,
        client_id="system-sensors-host",
        timezone="UTC",
        device_name="host",
    )


def test_parse_args_defaults() -> None:
    ns = installer._parse_args(["--config-path", "/tmp/foo"])
    assert ns.config_path == Path("/tmp/foo")
    assert ns.dry_run is False
    assert ns.non_interactive is False
    assert ns.log_level == "INFO"


def test_dry_run_writes_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "cfg"
    save_settings(settings_path(config_dir), _baseline_settings())

    _patch_probe(monkeypatch, ["sensor_a", "sensor_b"])
    rc = installer.main(
        [
            "--config-path",
            str(config_dir),
            "--dry-run",
            "--non-interactive",
        ]
    )
    assert rc == 0
    assert not sensors_enabled_path(config_dir).exists()
    out = capsys.readouterr().out
    assert "system_sensors install summary" in out
    assert "sensor_a" in out


def test_rerun_preserves_user_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    save_settings(settings_path(config_dir), _baseline_settings())

    from datetime import datetime, timezone

    save_sensors_enabled(
        sensors_enabled_path(config_dir),
        SensorsEnabled(
            sensors={
                "sensor_a": SensorEntry(enabled=False, available=True),
                "sensor_b": SensorEntry(enabled=True, available=True),
            },
            last_probe_utc=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
    )

    _patch_probe(monkeypatch, ["sensor_a", "sensor_b"])
    rc = installer.main(["--config-path", str(config_dir), "--non-interactive"])
    assert rc == 0

    loaded = load_sensors_enabled(sensors_enabled_path(config_dir))
    assert loaded is not None
    assert loaded.sensors["sensor_a"].enabled is False
    assert loaded.sensors["sensor_a"].available is True
    assert loaded.sensors["sensor_b"].enabled is True


def test_non_interactive_missing_required_flags_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "cfg"
    _patch_probe(monkeypatch, [])
    rc = installer.main(
        ["--config-path", str(config_dir), "--non-interactive"]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "--mqtt-hostname" in err
    assert "--client-id" in err
    assert "--timezone" in err
    assert "--device-name" in err


def test_non_interactive_with_all_flags_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _patch_probe(monkeypatch, ["sensor_a"])
    rc = installer.main(
        [
            "--config-path",
            str(config_dir),
            "--non-interactive",
            "--mqtt-hostname",
            "broker.local",
            "--client-id",
            "system-sensors-host",
            "--timezone",
            "UTC",
            "--device-name",
            "MyHost",
        ]
    )
    assert rc == 0
    loaded_settings = load_settings(settings_path(config_dir))
    assert loaded_settings is not None
    assert loaded_settings.mqtt_hostname == "broker.local"
    assert loaded_settings.device_name == "myhost"
    loaded_sensors = load_sensors_enabled(sensors_enabled_path(config_dir))
    assert loaded_sensors is not None
    assert "sensor_a" in loaded_sensors.sensors


def test_summary_includes_expected_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "cfg"
    save_settings(settings_path(config_dir), _baseline_settings())
    _patch_probe(monkeypatch, ["sensor_a", "sensor_b"])
    rc = installer.main(["--config-path", str(config_dir), "--non-interactive"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "system_sensors install summary" in out
    assert "Config dir:" in out
    assert "Sensors file:" in out
    assert "Last probe:" in out
    assert "Detected sensors" in out


def test_external_drives_injected_as_sensors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    settings = _baseline_settings()
    settings.external_drives = {"data": "/mnt/data", "backup": "/mnt/backup"}
    save_settings(settings_path(config_dir), settings)

    _patch_probe(monkeypatch, ["sensor_a"])
    rc = installer.main(["--config-path", str(config_dir), "--non-interactive"])
    assert rc == 0
    loaded = load_sensors_enabled(sensors_enabled_path(config_dir))
    assert loaded is not None
    assert "disk_use_data" in loaded.sensors
    assert "disk_use_backup" in loaded.sensors


def test_first_install_summary_uses_first_install_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "cfg"
    _patch_probe(monkeypatch, ["sensor_a"])
    rc = installer.main(
        [
            "--config-path",
            str(config_dir),
            "--non-interactive",
            "--mqtt-hostname",
            "broker.local",
            "--client-id",
            "system-sensors-host",
            "--timezone",
            "UTC",
            "--device-name",
            "host",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "First install" in out


def test_non_tty_without_non_interactive_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "cfg"
    _patch_probe(monkeypatch, [])
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    rc = installer.main(["--config-path", str(config_dir)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "stdin is not a TTY" in err
