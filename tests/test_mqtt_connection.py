#!/usr/bin/env python3
"""
test_mqtt_connection.py — Verify MQTT broker settings before running system_sensors.

Usage:
    python3 test_mqtt_connection.py                         # reads ~/.config/system_sensors/settings.yaml
    python3 test_mqtt_connection.py --host 192.168.1.10     # override host
    python3 test_mqtt_connection.py --host 192.168.1.10 --port 1883 --username user --password pass

Exit codes:
    0 — connected and disconnected cleanly
    1 — connection failed (check output for reason)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("ERROR: paho-mqtt not installed. Run: pip install paho-mqtt")
    sys.exit(1)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Load settings from file if present
# ---------------------------------------------------------------------------

def _load_settings_yaml(path: Path) -> dict:
    if yaml is None or not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception as e:
        print(f"  Warning: could not read {path}: {e}")
    return {}


def _settings_from_file() -> dict:
    candidates = [
        Path.home() / ".config" / "system_sensors" / "settings.yaml",
        Path(__file__).parent / "settings.yaml",
    ]
    for path in candidates:
        data = _load_settings_yaml(path)
        if data:
            print(f"  Loaded settings from: {path}")
            return data
    return {}


# ---------------------------------------------------------------------------
# MQTT test
# ---------------------------------------------------------------------------

_RESULT: dict = {"connected": False, "rc": None, "error": None}

_RC_STRINGS = {
    0: "Connection accepted",
    1: "Refused — incorrect protocol version",
    2: "Refused — invalid client identifier",
    3: "Refused — server unavailable",
    4: "Refused — bad username or password",
    5: "Refused — not authorized",
}


def _on_connect(client, userdata, flags, rc, properties=None):
    _RESULT["rc"] = rc
    if rc == 0:
        _RESULT["connected"] = True
    else:
        _RESULT["error"] = _RC_STRINGS.get(rc, f"Unknown rc={rc}")


def _on_connect_fail(client, userdata):
    _RESULT["error"] = "Connection failed (TCP-level — check host/port)"


def test_connection(
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    timeout: float = 8.0,
) -> bool:
    client = mqtt.Client(
        client_id="system_sensors_test",
        protocol=mqtt.MQTTv5,
    )
    client.on_connect = _on_connect
    client.on_connect_fail = _on_connect_fail

    if username:
        client.username_pw_set(username, password)

    print(f"\n  Connecting to {host}:{port} ...", end=" ", flush=True)
    try:
        client.connect(host, port, keepalive=10)
    except OSError as e:
        print(f"\n  ERROR: Could not reach broker — {e}")
        print("  Check that the host/port are correct and the broker is running.")
        return False

    deadline = time.time() + timeout
    client.loop_start()
    while time.time() < deadline:
        if _RESULT["connected"] or _RESULT["error"]:
            break
        time.sleep(0.1)
    client.loop_stop()

    if _RESULT["connected"]:
        client.disconnect()
        print("OK")
        return True

    if _RESULT["error"]:
        print(f"FAILED\n  Reason: {_RESULT['error']}")
    else:
        print(f"FAILED\n  Reason: Timed out after {timeout}s — broker unreachable or too slow")
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    file_settings = _settings_from_file()
    mqtt_block = file_settings.get("mqtt", {}) if isinstance(file_settings.get("mqtt"), dict) else {}

    parser = argparse.ArgumentParser(description="Test MQTT broker connectivity.")
    parser.add_argument("--host",     default=mqtt_block.get("hostname"),    help="Broker hostname or IP")
    parser.add_argument("--port",     default=mqtt_block.get("port", 1883),  type=int)
    parser.add_argument("--username", default=mqtt_block.get("username"),    help="MQTT username (optional)")
    parser.add_argument("--password", default=mqtt_block.get("password"),    help="MQTT password (optional)")
    args = parser.parse_args()

    print("\nMQTT connection test")
    print("=" * 40)
    print(f"  Host     : {args.host or '(not set)'}")
    print(f"  Port     : {args.port}")
    print(f"  Username : {args.username or '(none)'}")
    print(f"  Password : {'***' if args.password else '(none)'}")

    if not args.host:
        print("\n  ERROR: No broker host specified.")
        print("  Either set mqtt.hostname in settings.yaml or pass --host <ip>")
        return 1

    ok = test_connection(args.host, args.port, args.username, args.password)

    if ok:
        print("\n  Connection successful — your MQTT settings are correct.")
        return 0
    else:
        print("\n  Troubleshooting tips:")
        print("  - rc=4 (bad username/password): check mqtt.username and mqtt.password in settings.yaml")
        print("  - rc=5 (not authorized): broker ACL may be blocking this client_id or topic")
        print("  - TCP error: verify the broker is running and the IP/port are reachable")
        print("    e.g.  nc -zv <host> 1883")
        return 1


if __name__ == "__main__":
    sys.exit(main())
