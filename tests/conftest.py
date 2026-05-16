"""Shared pytest fixtures for system_sensors tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def utc_now_iso() -> str:
    """Return a fixed ISO-8601 string for deterministic annotation tests."""
    return "2026-05-14"
