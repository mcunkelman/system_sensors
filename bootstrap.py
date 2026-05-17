#!/usr/bin/env python3
"""bootstrap.py — One-command setup for system_sensors.

Run this script directly with the system Python (no venv needed first):

    python3 bootstrap.py

What it does:
    1. Checks Python version (>= 3.11 required)
    2. Checks python3-venv is available
    3. Creates a venv at a location the user confirms or overrides
    4. Installs system_sensors into the venv via pip
    5. Runs system-sensors-install (the interactive installer) inside the venv
       which probes hardware, collects MQTT settings, writes config files,
       and offers to install the systemd service

All subsequent commands use the venv's entrypoints:
    .venv/bin/system-sensors-detect   # pre-flight hardware report
    .venv/bin/system-sensors-install  # re-run after adding hardware
    .venv/bin/system-sensors-run      # start publishing

This script is stdlib-only and works before pip or paho-mqtt are installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_PYTHON = (3, 11)
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_VENV = SCRIPT_DIR / ".venv"
DEFAULT_CONFIG = Path.home() / ".config" / "system_sensors"


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

def _print_header() -> None:
    print()
    print("=" * 56)
    print("  system_sensors — bootstrap installer")
    print("=" * 56)
    print()


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _err(msg: str) -> None:
    print(f"  ✗ {msg}", file=sys.stderr)


def _info(msg: str) -> None:
    print(f"    {msg}")


def _step(n: int, total: int, label: str) -> None:
    print()
    print(f"[{n}/{total}] {label}")
    print("─" * 56)


def _prompt(label: str, default: str) -> str:
    try:
        raw = input(f"  {label} [{default}]: ").strip()
    except EOFError:
        raw = ""
    return raw or default


def _ask_yn(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    while True:
        try:
            raw = input(f"  {prompt}{suffix}: ").strip().lower()
        except EOFError:
            return default
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  Please enter y or n.")


# ---------------------------------------------------------------------------
# Step 1 — Python version check
# ---------------------------------------------------------------------------

def check_python_version() -> None:
    current = sys.version_info[:2]
    if current < MIN_PYTHON:
        _err(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, "
            f"found {current[0]}.{current[1]}."
        )
        _info("On Raspberry Pi OS / Ubuntu:")
        _info("  sudo apt install python3.11 python3.11-venv")
        _info("Then re-run with:  python3.11 bootstrap.py")
        sys.exit(1)
    _ok(f"Python {current[0]}.{current[1]} detected")


# ---------------------------------------------------------------------------
# Step 2 — Check venv module is available
# ---------------------------------------------------------------------------

def check_venv_available() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "venv", "--help"],
        capture_output=True,
    )
    if result.returncode != 0:
        _err("python3-venv is not installed.")
        _info("Install it with:")
        _info("  sudo apt install python3-venv python3-full")
        sys.exit(1)
    _ok("venv module available")


# ---------------------------------------------------------------------------
# Step 3 — Choose venv location
# ---------------------------------------------------------------------------

def choose_venv_path() -> Path:
    print()
    _info(f"Default venv location: {DEFAULT_VENV}")
    raw = _prompt("Venv path (press Enter to accept default)", str(DEFAULT_VENV))
    venv_path = Path(raw).expanduser().resolve()

    if venv_path.exists() and (venv_path / "bin" / "python").exists():
        print(f"  Existing venv found at {venv_path}")
        if _ask_yn("  Use existing venv?", default=True):
            _ok(f"Using existing venv: {venv_path}")
            return venv_path
        # User wants a fresh one — delete and recreate
        print(f"  Removing {venv_path} ...")
        shutil.rmtree(venv_path)

    return venv_path


# ---------------------------------------------------------------------------
# Step 4 — Choose config path
# ---------------------------------------------------------------------------

def choose_config_path() -> Path:
    print()
    _info(f"Default config location: {DEFAULT_CONFIG}")
    _info("This is where settings.yaml and sensors_enabled.yaml will be written.")
    raw = _prompt("Config path (press Enter to accept default)", str(DEFAULT_CONFIG))
    config_path = Path(raw).expanduser().resolve()
    _ok(f"Config path: {config_path}")
    return config_path


# ---------------------------------------------------------------------------
# Step 5 — Create venv
# ---------------------------------------------------------------------------

def create_venv(venv_path: Path) -> Path:
    """Create the venv and return the path to its Python interpreter."""
    print(f"  Creating venv at {venv_path} ...")
    result = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _err("Failed to create venv:")
        _info(result.stderr.strip())
        sys.exit(1)

    venv_python = venv_path / "bin" / "python"
    if not venv_python.is_file():
        _err(f"Venv created but {venv_python} not found.")
        sys.exit(1)

    _ok(f"Venv created: {venv_path}")
    return venv_python


# ---------------------------------------------------------------------------
# Step 6 — Install system_sensors into venv
# ---------------------------------------------------------------------------

def install_package(venv_python: Path) -> None:
    """pip install -e . into the venv."""
    print(f"  Installing system_sensors into venv ...")
    result = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet", "-e", str(SCRIPT_DIR)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _err("pip install failed:")
        _info(result.stderr.strip() or result.stdout.strip())
        sys.exit(1)
    _ok("system_sensors installed")


# ---------------------------------------------------------------------------
# Step 7 — Run the interactive installer inside the venv
# ---------------------------------------------------------------------------

def run_installer(venv_path: Path, config_path: Path) -> None:
    """Exec system-sensors-install from the venv."""
    installer = venv_path / "bin" / "system-sensors-install"
    if not installer.is_file():
        _err(f"Installer entrypoint not found: {installer}")
        _info("The package may not have installed correctly.")
        sys.exit(1)

    print()
    print("─" * 56)
    print("  Launching interactive installer ...")
    print("─" * 56)

    # Hand off to the installer — it handles hardware probe, MQTT prompts,
    # config writes, and the service_step at the end.
    result = subprocess.run(
        [str(installer), "--config-path", str(config_path)],
    )
    sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# Summary / next steps helper
# ---------------------------------------------------------------------------

def print_next_steps(venv_path: Path, config_path: Path) -> None:
    """Printed by the installer on success — kept here for reference."""
    print()
    print("─" * 56)
    print("  Bootstrap complete. Useful commands:")
    print("─" * 56)
    bin_dir = venv_path / "bin"
    print(f"  Pre-flight report:  {bin_dir}/system-sensors-detect")
    print(f"  Re-run installer:   {bin_dir}/system-sensors-install --config-path {config_path}")
    print(f"  Start publisher:    {bin_dir}/system-sensors-run --config-path {config_path}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    _print_header()

    total = 6

    _step(1, total, "Python version")
    check_python_version()

    _step(2, total, "venv module")
    check_venv_available()

    _step(3, total, "Venv location")
    venv_path = choose_venv_path()

    _step(4, total, "Config location")
    config_path = choose_config_path()

    _step(5, total, "Creating venv & installing")
    venv_python = create_venv(venv_path)
    install_package(venv_python)

    _step(6, total, "Running installer")
    run_installer(venv_path, config_path)
    # run_installer execs and does not return


if __name__ == "__main__":
    main()
