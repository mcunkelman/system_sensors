"""Tests for `system_sensors.mqtt_publisher`."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch

import paho.mqtt.client as mqtt
import pytest

from system_sensors.config import Settings
from system_sensors.mqtt_publisher import MqttPublisher


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = dict(
        mqtt_hostname="broker.local",
        mqtt_port=1883,
        client_id="system-sensors-host",
        timezone="UTC",
        device_name="host",
    )
    base.update(overrides)
    return Settings(**base)


def _make_client_mock() -> MagicMock:
    client = MagicMock()
    info = MagicMock()
    info.rc = mqtt.MQTT_ERR_SUCCESS
    client.publish.return_value = info
    return client


def test_init_skips_username_when_absent() -> None:
    client = _make_client_mock()
    with patch.object(mqtt, "Client", return_value=client):
        MqttPublisher(_settings())
    client.username_pw_set.assert_not_called()


def test_init_calls_username_pw_set_when_present() -> None:
    client = _make_client_mock()
    with patch.object(mqtt, "Client", return_value=client):
        MqttPublisher(_settings(mqtt_username="u", mqtt_password="p"))
    client.username_pw_set.assert_called_once_with("u", "p")


def test_init_skips_tls_when_no_ca_certs() -> None:
    client = _make_client_mock()
    with patch.object(mqtt, "Client", return_value=client):
        MqttPublisher(_settings())
    client.tls_set.assert_not_called()


def test_init_calls_tls_set_when_ca_certs_present() -> None:
    client = _make_client_mock()
    with patch.object(mqtt, "Client", return_value=client):
        MqttPublisher(
            _settings(
                tls_ca_certs="/etc/ssl/ca.pem",
                tls_certfile="/etc/ssl/c.pem",
                tls_keyfile="/etc/ssl/k.pem",
            )
        )
    client.tls_set.assert_called_once_with(
        ca_certs="/etc/ssl/ca.pem",
        certfile="/etc/ssl/c.pem",
        keyfile="/etc/ssl/k.pem",
    )


def test_init_always_sets_will() -> None:
    client = _make_client_mock()
    with patch.object(mqtt, "Client", return_value=client):
        MqttPublisher(_settings())
    client.will_set.assert_called_once()
    kwargs = client.will_set.call_args.kwargs
    assert kwargs["topic"] == "system-sensors/host/availability"
    assert kwargs["payload"] == b"offline"
    assert kwargs["retain"] is True


def test_publish_dict_payload_is_json_encoded() -> None:
    client = _make_client_mock()
    with patch.object(mqtt, "Client", return_value=client):
        pub = MqttPublisher(_settings())
    pub.publish("topic", {"a": 1, "b": "x"})
    args, kwargs = client.publish.call_args
    assert args[0] == "topic"
    assert json.loads(args[1]) == {"a": 1, "b": "x"}
    assert kwargs["qos"] == 1
    assert kwargs["retain"] is False


def test_publish_bytes_payload_passes_through() -> None:
    client = _make_client_mock()
    with patch.object(mqtt, "Client", return_value=client):
        pub = MqttPublisher(_settings())
    pub.publish("topic", b"raw", retain=True, qos=0)
    args, kwargs = client.publish.call_args
    assert args[0] == "topic"
    assert args[1] == b"raw"
    assert kwargs["qos"] == 0
    assert kwargs["retain"] is True


def test_publish_str_payload_passes_through() -> None:
    client = _make_client_mock()
    with patch.object(mqtt, "Client", return_value=client):
        pub = MqttPublisher(_settings())
    pub.publish("topic", "hello")
    args, _kwargs = client.publish.call_args
    assert args[1] == "hello"


def test_publish_swallows_exceptions() -> None:
    client = _make_client_mock()
    client.publish.side_effect = RuntimeError("boom")
    with patch.object(mqtt, "Client", return_value=client):
        pub = MqttPublisher(_settings())
    pub.publish("topic", b"x")


def test_connect_retries_with_backoff_then_succeeds() -> None:
    client = _make_client_mock()

    calls = {"connect": 0}

    def fake_connect(host: str, port: int, keepalive: int = 60) -> None:
        calls["connect"] += 1
        if calls["connect"] < 3:
            raise OSError("connection refused")

    client.connect.side_effect = fake_connect

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    with patch.object(mqtt, "Client", return_value=client):
        pub = MqttPublisher(_settings())

    pub._connected.set()

    async def run() -> None:
        with patch("system_sensors.mqtt_publisher.asyncio.sleep", new=fake_sleep):
            await pub.connect()

    asyncio.run(run())

    assert calls["connect"] == 3
    assert len(sleeps) == 2
    # First retry around 5s, second around 10s (with +/- 20% jitter).
    assert 4.0 <= sleeps[0] <= 6.0
    assert 8.0 <= sleeps[1] <= 12.0


def test_on_connect_sets_event_on_success() -> None:
    client = _make_client_mock()
    with patch.object(mqtt, "Client", return_value=client):
        pub = MqttPublisher(_settings())
    pub._connected.clear()
    rc = MagicMock()
    rc.is_failure = False
    pub._on_connect(client, None, None, rc)
    assert pub._connected.is_set()


def test_on_connect_legacy_rc_zero_sets_event() -> None:
    client = _make_client_mock()
    with patch.object(mqtt, "Client", return_value=client):
        pub = MqttPublisher(_settings())
    pub._connected.clear()
    pub._on_connect(client, None, None, 0)
    assert pub._connected.is_set()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
