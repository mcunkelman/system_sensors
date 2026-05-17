"""Async-friendly wrapper around paho-mqtt's threaded client.

Wraps `paho-mqtt` v2 in an asyncio-friendly shape. Responsibilities:

- Connect with exponential backoff + jitter (NOT the upstream 120 s / 600 s
  hard sleeps; see port-surface.md "Connect-retry").
- Publish state payloads as bare UTF-8 strings on a per-sensor topic.
- Publish HA-discovery payloads as JSON on configured topic with retain=True.
- Track an `availability` topic and a last-will message of `offline`.

paho's `loop_start()` runs its network thread in the background. `publish()`
is thread-safe and non-blocking. The async/sync bridge is:
`client.connect`/`client.loop_*`/`client.disconnect` are blocking and so are
run in the default executor; `publish` is called inline from async code.

This module is the ONLY place that imports `paho.mqtt`. Everything else is
agnostic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import threading
from typing import Any

import paho.mqtt.client as mqtt

from system_sensors.config import Settings
from system_sensors.ha_discovery import availability_topic

_log = logging.getLogger(__name__)


class MqttPublisher:
    """Async-friendly wrapper around paho-mqtt's threaded client.

    Lifecycle:
        publisher = MqttPublisher(settings)
        await publisher.connect()
        publisher.publish(topic, payload, retain=False)
        await publisher.disconnect()
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._availability_topic = availability_topic(settings.device_name)
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.client_id,
            clean_session=True,
        )
        if settings.mqtt_username:
            self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        if settings.tls_ca_certs:
            self._client.tls_set(
                ca_certs=settings.tls_ca_certs,
                certfile=settings.tls_certfile,
                keyfile=settings.tls_keyfile,
            )
        self._client.will_set(
            topic=self._availability_topic,
            payload=b"offline",
            qos=1,
            retain=True,
        )
        self._connected = threading.Event()
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._loop_started = False

    def _on_connect(
        self,
        client: Any,
        userdata: Any,
        flags: Any,
        rc: Any,
        properties: Any = None,
    ) -> None:
        is_failure = getattr(rc, "is_failure", None)
        if is_failure is False or rc == 0:
            self._connected.set()
            _log.info(
                "MQTT connected to %s:%s",
                self._settings.mqtt_hostname,
                self._settings.mqtt_port,
            )
        else:
            _log.warning("MQTT connect rc=%s", rc)

    def _on_disconnect(
        self,
        client: Any,
        userdata: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self._connected.clear()
        _log.info("MQTT disconnected")

    async def connect(self) -> None:
        """Connect with exponential backoff + jitter until success.

        Backoff: 5, 10, 20, 40, 80, 160, 300 (capped). Each delay gets +/- 20%
        jitter. Cancellable via asyncio.CancelledError.
        """
        delay = 5.0
        attempt = 0
        loop = asyncio.get_running_loop()
        while True:
            attempt += 1
            try:
                await loop.run_in_executor(
                    None,
                    lambda: self._client.connect(
                        self._settings.mqtt_hostname,
                        self._settings.mqtt_port,
                        keepalive=60,
                    ),
                )
                if not self._loop_started:
                    self._client.loop_start()
                    self._loop_started = True
                got = await loop.run_in_executor(
                    None, lambda: self._connected.wait(timeout=10.0)
                )
                if got:
                    self.publish(self._availability_topic, b"online", retain=True)
                    return
                _log.warning("connect attempt %d timed out waiting for on_connect", attempt)
            except Exception as exc:
                _log.warning("connect attempt %d failed: %s", attempt, exc)

            jitter = random.uniform(-0.2, 0.2) * delay
            sleep_for = max(1.0, delay + jitter)
            _log.info("retrying connect in %.1fs", sleep_for)
            await asyncio.sleep(sleep_for)
            delay = min(delay * 2.0, 300.0)

    def publish(
        self,
        topic: str,
        payload: bytes | str | dict[str, Any],
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        """Thread-safe publish. Non-blocking. Errors are logged, not raised."""
        try:
            if isinstance(payload, dict):
                encoded: bytes | str = json.dumps(payload, separators=(",", ":"))
            else:
                encoded = payload
            info = self._client.publish(topic, encoded, qos=qos, retain=retain)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                _log.warning("publish %s rc=%s", topic, info.rc)
        except Exception:
            _log.exception("publish %s failed", topic)

    async def disconnect(self) -> None:
        """Graceful: publish offline (retain), stop loop, return."""
        try:
            self.publish(self._availability_topic, b"offline", retain=True)
        except Exception:
            _log.exception("publish offline failed during disconnect")
        loop = asyncio.get_running_loop()
        if self._loop_started:
            try:
                await loop.run_in_executor(None, self._client.loop_stop)
            except Exception:
                _log.exception("loop_stop failed")
            self._loop_started = False
        try:
            await loop.run_in_executor(None, self._client.disconnect)
        except Exception:
            _log.exception("client.disconnect failed")
