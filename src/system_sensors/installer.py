"""CLI entrypoint: `system-sensors-install`.
 
Probes the host, merges against any existing `sensors_enabled.yaml` (per PLAN.md
merge semantics), and writes the generated config. On first run also seeds
`settings.yaml` via interactive prompts (or non-interactive CLI flags).
Subsequent runs never touch `settings.yaml`.
"""
 
from __future__ import annotations
 
import argparse
import logging
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
 
from system_sensors.config import (
    ConfigError,
    SensorsEnabled,
    Settings,
    load_sensors_enabled,
    load_settings,
    save_sensors_enabled,
    save_settings,
    sensors_enabled_path,
    settings_path,
)
from system_sensors.merge import MergeResult, merge_probe_with_existing
from system_sensors.registry import (
    discover_sensors,
    instantiate_active_sensors,
    probe_all,
    select_variants,
)
from system_sensors.sensors.base import Sensor
from system_sensors.sensors.disk import DiskUseMount
from system_sensors.service import service_step
 
 
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "system_sensors"
_LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR")
 
_log = logging.getLogger(__name__)
 
 
def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for the installer CLI."""
    parser = argparse.ArgumentParser(
        prog="system-sensors-install",
        description=(
            "Probe host capabilities and write the system_sensors configuration."
        ),
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=(
            "Directory holding settings.yaml and sensors_enabled.yaml. "
            f"Default: {DEFAULT_CONFIG_PATH}"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the diff without writing any files.",
    )
    parser.add_argument(
        "--reprobe",
        action="store_true",
        help=(
            "Alias for re-running the installer. The installer always re-probes "
            "on every invocation; this flag is for script clarity only."
        ),
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Fail instead of prompting for missing required settings.",
    )
    parser.add_argument(
        "--no-service",
        action="store_true",
        help="Skip the systemd service setup step.",
    )
    parser.add_argument(
        "--venv-path",
        type=Path,
        default=None,
        help="Path to the venv directory. Used to write the correct Python path into the systemd service file. Set automatically by bootstrap.py.",
    )
    parser.add_argument(
        "--log-level",
        choices=_LOG_LEVELS,
        default="INFO",
        help="Logging verbosity. Default: INFO.",
    )
 
    parser.add_argument("--mqtt-hostname", type=str, default=None)
    parser.add_argument("--mqtt-port", type=int, default=None)
    parser.add_argument("--mqtt-username", type=str, default=None)
    parser.add_argument("--mqtt-password", type=str, default=None)
    parser.add_argument("--client-id", type=str, default=None)
    parser.add_argument("--timezone", type=str, default=None)
    parser.add_argument("--device-name", type=str, default=None)
    parser.add_argument(
        "--update-interval",
        type=int,
        default=None,
        help="Publish interval in seconds. Default: 60.",
    )
    return parser
 
 
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse argv and return the resulting Namespace."""
    return _build_parser().parse_args(argv)
 
 
def _hostname_default() -> str:
    try:
        return socket.gethostname().split(".")[0] or "system-sensors"
    except OSError:
        return "system-sensors"
 
 
def _prompt(label: str, default: str | None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            raw = input(f"{label}{suffix}: ").strip()
        except EOFError:
            raw = ""
        value = raw or (default or "")
        if value:
            return value
        print(f"  {label} is required, please provide a value.", file=sys.stderr)
 
 
def _prompt_port(default: int = 1883) -> int:
    while True:
        try:
            raw = input(f"MQTT broker port [{default}]: ").strip()
        except EOFError:
            raw = ""
        if not raw:
            return default
        try:
            port = int(raw)
        except ValueError:
            print("  Port must be an integer.", file=sys.stderr)
            continue
        if port < 1 or port > 65535:
            print("  Port must be between 1 and 65535.", file=sys.stderr)
            continue
        return port
 
 
def _prompt_timezone(default: str = "UTC") -> str:
    while True:
        value = _prompt("IANA timezone", default)
        try:
            ZoneInfo(value)
            return value
        except (ZoneInfoNotFoundError, ValueError):
            print(f"  Unknown IANA timezone {value!r}, try again.", file=sys.stderr)
 
 
def _interactive_settings() -> Settings:
    """Walk the user through the required settings.yaml fields."""
    hostname_default = _hostname_default()
    print("First install detected — collecting required settings.")
    mqtt_hostname = _prompt("MQTT broker hostname", None)
    mqtt_port = _prompt_port(1883)
    client_id = _prompt("Client ID", f"system-sensors-{hostname_default}")
    tz = _prompt_timezone("UTC")
    device_name = _prompt("Device name", hostname_default).lower()
    return Settings(
        mqtt_hostname=mqtt_hostname,
        mqtt_port=mqtt_port,
        client_id=client_id,
        timezone=tz,
        device_name=device_name,
    )
 
 
def _settings_from_args(args: argparse.Namespace) -> Settings:
    """Build a Settings dataclass purely from CLI flags. Raises ConfigError if missing."""
    missing: list[str] = []
    if not args.mqtt_hostname:
        missing.append("--mqtt-hostname")
    if not args.client_id:
        missing.append("--client-id")
    if not args.timezone:
        missing.append("--timezone")
    if not args.device_name:
        missing.append("--device-name")
    if missing:
        raise ConfigError(
            "settings.yaml not found and required flags are missing: "
            + ", ".join(missing)
        )
 
    port = args.mqtt_port if args.mqtt_port is not None else 1883
    if port < 1 or port > 65535:
        raise ConfigError(f"--mqtt-port must be between 1 and 65535 (got {port})")
    try:
        ZoneInfo(args.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"Invalid --timezone {args.timezone!r}: {exc}") from exc
 
    return Settings(
        mqtt_hostname=args.mqtt_hostname,
        mqtt_port=port,
        mqtt_username=args.mqtt_username,
        mqtt_password=args.mqtt_password,
        client_id=args.client_id,
        timezone=args.timezone,
        update_interval=args.update_interval if args.update_interval is not None else 60,
        device_name=args.device_name.lower(),
    )
 
 
def _build_external_drive_sensors(settings: Settings) -> list[Sensor]:
    """Instantiate DiskUseMount instances for each entry in external_drives."""
    out: list[Sensor] = []
    for name, mount in settings.external_drives.items():
        out.append(DiskUseMount(mount_point=mount, logical_name=f"disk_use_{name}"))
    return out
 
 
def _probe_host() -> list[Sensor]:
    """Run discovery + probe + variant selection. Returns ready-to-publish sensors."""
    classes = discover_sensors()
    probed = probe_all(classes)
    selected = select_variants(probed)
    return instantiate_active_sensors(selected)
 
 
def _resolve_settings(
    args: argparse.Namespace, path: Path
) -> tuple[Settings, bool]:
    """Load existing settings.yaml or build new ones. Returns (settings, newly_created)."""
    existing = load_settings(path)
    if existing is not None:
        return existing, False
 
    if args.non_interactive or not sys.stdin.isatty():
        if not args.non_interactive:
            raise ConfigError(
                "settings.yaml not found and stdin is not a TTY. "
                "Re-run with --non-interactive and --mqtt-hostname=..., "
                "--client-id=..., --timezone=..., --device-name=..."
            )
        return _settings_from_args(args), True
 
    return _interactive_settings(), True
 
 
def _print_summary(
    *,
    config_dir: Path,
    settings_status: str,
    sensors_status: str,
    instances: list[Sensor],
    merge: MergeResult,
    is_first_install: bool,
) -> None:
    """Render the end-of-run install summary to stdout."""
    names = sorted(inst.resolved_logical_name() for inst in instances)
    last_probe = merge.new_state.last_probe_utc
    if last_probe.tzinfo is None:
        last_probe = last_probe.replace(tzinfo=timezone.utc)
 
    print("system_sensors install summary")
    print("==============================")
    print(f"Config dir:        {config_dir}")
    print(f"Settings file:     {settings_status}")
    print(f"Sensors file:      {sensors_status}")
    print(f"Last probe:        {last_probe.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print()
    print(f"Detected sensors ({len(names)}):")
    if names:
        for chunk in _wrap_names(names, width=72):
            print(f"  {chunk}")
    else:
        print("  (none)")
    print()
    if is_first_install:
        print("First install — all detected sensors enabled.")
    else:
        print("Changes since last run:")
        print(f"  + Added ({len(merge.added)}):       {', '.join(merge.added)}")
        print(
            f"  ~ Now unavailable ({len(merge.became_unavailable)}): "
            f"{', '.join(merge.became_unavailable)}"
        )
        print(
            f"  ~ Available again ({len(merge.became_available_again)}): "
            f"{', '.join(merge.became_available_again)}"
        )
        print(f"  = Unchanged ({len(merge.kept)})")
    print()
    print("Next: run `system-sensors-run` to start publishing.")
    print("      Or let the installer set up a systemd service for you.")
 
 
def _wrap_names(names: list[str], *, width: int) -> list[str]:
    """Wrap a comma-joined list of names to lines no wider than `width`."""
    lines: list[str] = []
    current = ""
    for name in names:
        token = name + ", "
        if not current:
            current = token
            continue
        if len(current) + len(token) > width:
            lines.append(current.rstrip().rstrip(","))
            current = token
        else:
            current += token
    if current:
        lines.append(current.rstrip().rstrip(","))
    return lines
 
 
def _install(args: argparse.Namespace) -> int:
    """Top-level install flow. Returns process exit code."""
    config_dir: Path = args.config_path
    s_path = settings_path(config_dir)
    se_path = sensors_enabled_path(config_dir)
 
    existing_settings = load_settings(s_path)
    if existing_settings is None:
        settings, _ = _resolve_settings(args, s_path)
        settings_status = "newly created"
        if args.dry_run:
            settings_status = "would create (dry run)"
        else:
            save_settings(s_path, settings)
    else:
        settings = existing_settings
        settings_status = "unchanged"
 
    instances = _probe_host()
    instances.extend(_build_external_drive_sensors(settings))
 
    probed_names: set[str] = {inst.resolved_logical_name() for inst in instances}
 
    existing_sensors_enabled = load_sensors_enabled(se_path)
    now = datetime.now(timezone.utc)
    merge = merge_probe_with_existing(
        probed_sensor_names=probed_names,
        existing=existing_sensors_enabled,
        now_utc=now,
    )
 
    is_first_install = existing_sensors_enabled is None
    if args.dry_run:
        sensors_status = "unchanged (dry run)"
    else:
        save_sensors_enabled(se_path, merge.new_state)
        sensors_status = "newly created" if is_first_install else "updated"
 
    _print_summary(
        config_dir=config_dir,
        settings_status=settings_status,
        sensors_status=sensors_status,
        instances=instances,
        merge=merge,
        is_first_install=is_first_install,
    )
 
    # ── Systemd service step ─────────────────────────────────────────────
    if not args.dry_run and not getattr(args, "no_service", False):
        interactive = not args.non_interactive and sys.stdin.isatty()
        venv_path = getattr(args, "venv_path", None)
        service_step(
            config_path=config_dir,
            venv_path=venv_path,
            interactive=interactive,
        )
 
    return 0
 
 
def main(argv: list[str] | None = None) -> int:
    """Installer entrypoint. Returns process exit code."""
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )
 
    try:
        return _install(args)
    except ConfigError as exc:
        print(f"system-sensors-install: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - top-level CLI guard
        _log.exception("Unhandled error in installer")
        print(f"system-sensors-install: unexpected error: {exc}", file=sys.stderr)
        return 1
 
 
def _module_marker() -> dict[str, Any]:
    """Marker function kept for the test harness; do not remove."""
    return {"module": __name__}
 
 
if __name__ == "__main__":
    raise SystemExit(main())