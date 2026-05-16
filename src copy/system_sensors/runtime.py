"""CLI entrypoint: `system-sensors-run`.

Loads `settings.yaml` + `sensors_enabled.yaml`, instantiates only the listed
sensors, and runs the asyncio publish loop. No probing at runtime.

Single global cadence (`settings.update_interval`). All active sensors are
collected concurrently each cycle via `asyncio.gather(..., return_exceptions=
True)`; stray exceptions from a sensor are treated as unavailable.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from system_sensors.config import (
    ConfigError,
    SensorsEnabled,
    Settings,
    load_sensors_enabled,
    load_settings,
)
from system_sensors.ha_discovery import (
    build_discovery_payload,
    discovery_topic,
    state_topic,
)
from system_sensors.mqtt_publisher import MqttPublisher
from system_sensors.registry import (
    discover_sensors,
    instantiate_active_sensors,
    probe_all,
    select_variants,
)
from system_sensors.sensors.base import Sensor
from system_sensors.sensors.disk import DiskUseMount


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "system_sensors"

_LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_log = logging.getLogger("system_sensors")


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for the runtime CLI."""
    parser = argparse.ArgumentParser(
        prog="system-sensors-run",
        description=(
            "Run the system_sensors publish loop using the configuration produced "
            "by system-sensors-install."
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
        "--log-level",
        choices=_LOG_LEVELS,
        default="INFO",
        help="Logging verbosity. Default: INFO.",
    )
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    """Runtime entrypoint.

    Returns:
        Process exit code. Zero on clean shutdown, non-zero on fatal error.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 0


async def _run(args: argparse.Namespace) -> int:
    settings_path = args.config_path / "settings.yaml"
    sensors_path = args.config_path / "sensors_enabled.yaml"

    try:
        settings = load_settings(settings_path)
        state = load_sensors_enabled(sensors_path)
    except ConfigError as exc:
        _log.error("config error: %s", exc)
        return 1
    if settings is None:
        _log.error(
            "settings.yaml missing at %s -- run system-sensors-install first",
            settings_path,
        )
        return 1
    if state is None:
        _log.error("sensors_enabled.yaml missing -- run system-sensors-install first")
        return 1

    active = _build_active_sensors(settings, state)
    if not active:
        _log.warning("no sensors enabled+available; nothing to publish")
        return 0
    _log.info("starting with %d active sensors", len(active))

    publisher = MqttPublisher(settings)
    await publisher.connect()
    _publish_discovery(publisher, settings, active)

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    try:
        await _publish_loop(publisher, settings, active, stop_event)
    finally:
        await publisher.disconnect()
    return 0


def _build_active_sensors(settings: Settings, state: SensorsEnabled) -> list[Sensor]:
    """Discover/probe/select/instantiate; inject configured drives; filter enabled+available."""
    discovered = discover_sensors()
    probed = probe_all(discovered)
    selected = select_variants(probed)
    instances = instantiate_active_sensors(selected)

    for name, mount_point in settings.external_drives.items():
        instances.append(
            DiskUseMount(mount_point=mount_point, logical_name=f"disk_use_{name}")
        )

    active: list[Sensor] = []
    for sensor in instances:
        entry = state.sensors.get(sensor.resolved_logical_name())
        if entry is None:
            _log.debug(
                "sensor %s not in sensors_enabled.yaml; skipping",
                sensor.resolved_logical_name(),
            )
            continue
        if not entry.enabled or not entry.available:
            continue
        active.append(sensor)
    return active


def _publish_discovery(
    publisher: MqttPublisher,
    settings: Settings,
    active: list[Sensor],
) -> None:
    for sensor in active:
        topic = discovery_topic(
            sensor.resolved_logical_name(),
            settings.device_name,
            sensor_type=type(sensor).sensor_type,
        )
        payload = build_discovery_payload(sensor, settings)
        publisher.publish(topic, payload, retain=True, qos=1)
    _log.info("published HA discovery for %d sensors", len(active))


async def _publish_loop(
    publisher: MqttPublisher,
    settings: Settings,
    active: list[Sensor],
    stop_event: asyncio.Event,
) -> None:
    interval = max(1, settings.update_interval)
    while not stop_event.is_set():
        loop = asyncio.get_running_loop()
        cycle_start = loop.time()
        readings = await asyncio.gather(
            *(s.collect() for s in active), return_exceptions=True
        )
        for sensor, reading in zip(active, readings):
            if isinstance(reading, BaseException):
                _log.exception(
                    "sensor %s raised in collect",
                    sensor.resolved_logical_name(),
                    exc_info=reading,
                )
                continue
            if reading.unavailable or reading.value is None:
                continue
            topic = state_topic(sensor.resolved_logical_name(), settings.device_name)
            try:
                publisher.publish(topic, _encode_value(reading.value), retain=False, qos=0)
            except Exception:
                _log.exception(
                    "publish failed for sensor %s",
                    sensor.resolved_logical_name(),
                )

        elapsed = loop.time() - cycle_start
        remaining = interval - elapsed
        if remaining > 0:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                continue
        else:
            _log.warning(
                "cycle took %.2fs (interval %ds) -- falling behind", elapsed, interval
            )


def _encode_value(value: object) -> str:
    """Stringify a sensor value: bool -> on/off, float via repr, others via str."""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, int):
        return repr(value)
    return str(value)


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass


if __name__ == "__main__":
    sys.exit(main())
