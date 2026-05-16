"""Tests for network sensors."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import psutil
import pytest

from system_sensors.sensors.base import SensorReading
from system_sensors.sensors.network import (
    NetRxIface,
    NetRxTotal,
    NetTxIface,
    NetTxTotal,
    _real_interfaces,
)


# ---------------------------------------------------------------------------
# Aggregate sensors (existing)
# ---------------------------------------------------------------------------

def test_net_tx_probe_true_on_host() -> None:
    assert NetTxTotal.probe() is True


def test_net_rx_probe_true_on_host() -> None:
    assert NetRxTotal.probe() is True


async def test_net_tx_collect_warmup_returns_zero_then_value() -> None:
    sensor = NetTxTotal()
    first = await sensor.collect()
    assert isinstance(first, SensorReading)
    assert first.unavailable is False
    assert first.value == 0.0
    await asyncio.sleep(0.05)
    second = await sensor.collect()
    assert second.unavailable is False
    assert isinstance(second.value, float)
    assert second.value >= 0.0


async def test_net_rx_collect_warmup_returns_zero_then_value() -> None:
    sensor = NetRxTotal()
    first = await sensor.collect()
    assert first.unavailable is False
    assert first.value == 0.0
    await asyncio.sleep(0.05)
    second = await sensor.collect()
    assert second.unavailable is False
    assert isinstance(second.value, float)
    assert second.value >= 0.0


def test_net_sensors_have_distinct_logical_names() -> None:
    assert NetTxTotal().resolved_logical_name() == "net_tx_total"
    assert NetRxTotal().resolved_logical_name() == "net_rx_total"


# ---------------------------------------------------------------------------
# Per-interface helpers
# ---------------------------------------------------------------------------

def test_real_interfaces_excludes_loopback() -> None:
    ifaces = _real_interfaces()
    assert "lo" not in ifaces


def test_real_interfaces_excludes_virtual_prefixes() -> None:
    ifaces = _real_interfaces()
    for iface in ifaces:
        assert not iface.startswith("veth")
        assert not iface.startswith("docker")
        assert not iface.startswith("virbr")
        assert not iface.startswith("br-")
        assert not iface.startswith("tun")


def test_real_interfaces_returns_sorted_list() -> None:
    ifaces = _real_interfaces()
    assert ifaces == sorted(ifaces)


# ---------------------------------------------------------------------------
# Per-interface sensors — probe and enumeration
# ---------------------------------------------------------------------------

def test_net_tx_iface_probe_true_when_interfaces_exist() -> None:
    assert NetTxIface.probe() is True


def test_net_rx_iface_probe_true_when_interfaces_exist() -> None:
    assert NetRxIface.probe() is True


def test_net_tx_iface_enumerate_returns_one_per_interface() -> None:
    instances = NetTxIface.enumerate_instances()
    real = _real_interfaces()
    assert len(instances) == len(real)


def test_net_rx_iface_enumerate_returns_one_per_interface() -> None:
    instances = NetRxIface.enumerate_instances()
    real = _real_interfaces()
    assert len(instances) == len(real)


def test_net_tx_iface_resolved_names() -> None:
    instances = NetTxIface.enumerate_instances()
    real = _real_interfaces()
    for inst, iface in zip(instances, real):
        assert inst.resolved_logical_name() == f"net_tx_{iface}"


def test_net_rx_iface_resolved_names() -> None:
    instances = NetRxIface.enumerate_instances()
    real = _real_interfaces()
    for inst, iface in zip(instances, real):
        assert inst.resolved_logical_name() == f"net_rx_{iface}"


def test_per_iface_names_distinct_from_totals() -> None:
    tx_iface_names = {i.resolved_logical_name() for i in NetTxIface.enumerate_instances()}
    rx_iface_names = {i.resolved_logical_name() for i in NetRxIface.enumerate_instances()}
    assert "net_tx_total" not in tx_iface_names
    assert "net_rx_total" not in rx_iface_names


# ---------------------------------------------------------------------------
# Per-interface sensors — collect behaviour
# ---------------------------------------------------------------------------

async def test_net_tx_iface_warmup_returns_zero() -> None:
    ifaces = _real_interfaces()
    if not ifaces:
        pytest.skip("no real interfaces on this host")
    sensor = NetTxIface(ifaces[0])
    reading = await sensor.collect()
    assert reading.unavailable is False
    assert reading.value == 0.0


async def test_net_rx_iface_warmup_returns_zero() -> None:
    ifaces = _real_interfaces()
    if not ifaces:
        pytest.skip("no real interfaces on this host")
    sensor = NetRxIface(ifaces[0])
    reading = await sensor.collect()
    assert reading.unavailable is False
    assert reading.value == 0.0


async def test_net_tx_iface_second_collect_non_negative() -> None:
    ifaces = _real_interfaces()
    if not ifaces:
        pytest.skip("no real interfaces on this host")
    sensor = NetTxIface(ifaces[0])
    await sensor.collect()  # warmup
    await asyncio.sleep(0.05)
    reading = await sensor.collect()
    assert reading.unavailable is False
    assert isinstance(reading.value, float)
    assert reading.value >= 0.0


async def test_net_rx_iface_second_collect_non_negative() -> None:
    ifaces = _real_interfaces()
    if not ifaces:
        pytest.skip("no real interfaces on this host")
    sensor = NetRxIface(ifaces[0])
    await sensor.collect()  # warmup
    await asyncio.sleep(0.05)
    reading = await sensor.collect()
    assert reading.unavailable is False
    assert isinstance(reading.value, float)
    assert reading.value >= 0.0


async def test_missing_interface_returns_unavailable() -> None:
    """Interface present at probe time but gone at collect time → unavailable."""
    sensor = NetTxIface("nonexistent_iface99")
    # Prime state so it's not warmup
    sensor._last_bytes = 1000
    from datetime import datetime, timezone
    sensor._last_timestamp = datetime.now(timezone.utc)

    reading = await sensor.collect()
    assert reading.unavailable is True
    # State should be reset so next call is a clean warmup
    assert sensor._last_bytes is None
    assert sensor._last_timestamp is None


async def test_counter_wraparound_resets_and_returns_zero() -> None:
    """Simulate counter wraparound: current < last → return 0, reset state."""
    ifaces = _real_interfaces()
    if not ifaces:
        pytest.skip("no real interfaces on this host")

    sensor = NetTxIface(ifaces[0])
    from datetime import datetime, timezone

    # Manually prime with a huge last_bytes value to force wraparound
    sensor._last_bytes = 10 ** 18
    sensor._last_timestamp = datetime.now(timezone.utc)

    reading = await sensor.collect()
    assert reading.unavailable is False
    assert reading.value == 0.0


# ---------------------------------------------------------------------------
# Probe returns False when no real interfaces exist (mocked)
# ---------------------------------------------------------------------------

def test_net_iface_probe_false_when_no_interfaces() -> None:
    with patch(
        "system_sensors.sensors.network._real_interfaces", return_value=[]
    ):
        assert NetTxIface.probe() is False
        assert NetRxIface.probe() is False
