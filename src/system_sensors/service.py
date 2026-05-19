"""Systemd service file generation and installation for system_sensors.
 
Generates a `.service` unit pointing at the venv Python and the
`system-sensors-run` entrypoint. Either installs it automatically (requires
passwordless sudo) or prints the exact manual commands needed.
 
Public API used by installer.py:
    service_step(config_path, interactive) -> None
"""
 
from __future__ import annotations
 
import logging
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
 
_log = logging.getLogger(__name__)
 
SERVICE_NAME     = "system_sensors"
SERVICE_FILENAME = f"{SERVICE_NAME}.service"
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")
 
 
# ---------------------------------------------------------------------------
# Service file content
# ---------------------------------------------------------------------------
 
def build_service_file(
    *,
    run_as_user: str,
    venv_python: Path,
    config_path: Path,
    description: str = "system_sensors MQTT publisher",
) -> str:
    """Return the contents of a systemd unit file as a string.
 
    Args:
        run_as_user: Linux user account the service runs as.
        venv_python: Absolute path to the venv Python interpreter.
        config_path: Directory containing settings.yaml /
                     sensors_enabled.yaml.
        description: Human-readable [Unit] Description line.
    """
    exec_start = (
        f"{venv_python} -m system_sensors.runtime "
        f"--config-path {config_path}"
    )
    return textwrap.dedent(f"""\
        [Unit]
        Description={description}
        After=network-online.target
        Wants=network-online.target
 
        [Service]
        User={run_as_user}
        Type=simple
        ExecStart={exec_start}
        Restart=on-failure
        RestartSec=10
        StandardOutput=journal
        StandardError=journal
 
        [Install]
        WantedBy=multi-user.target
    """)
 
 
# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------
 
def _current_user() -> str:
    # SUDO_USER holds the original user when running under sudo
    return (
        os.environ.get("SUDO_USER")
        or os.environ.get("USER")
        or os.environ.get("LOGNAME")
        or "pi"
    )
 
 
def _venv_python() -> Path:
    """Return the Python interpreter for the active venv.
 
    Tries in order:
    1. VIRTUAL_ENV environment variable (set by `activate`) — most reliable
    2. sys.executable if it looks like it is inside a venv (contains .venv
       or venv in its path)
    3. Walk up from the package location looking for a .venv directory
 
    Falls back to sys.executable if nothing else is found — caller should
    warn if the result looks like a system Python.
    """
    # 1. Active venv via environment variable
    venv_env = os.environ.get("VIRTUAL_ENV")
    if venv_env:
        for name in ("python3", "python"):
            candidate = Path(venv_env) / "bin" / name
            if candidate.is_file():
                return candidate.resolve()
 
    # 2. sys.executable already inside a venv path
    exe = Path(sys.executable).resolve()
    exe_str = str(exe)
    if ".venv" in exe_str or "/venv/" in exe_str:
        return exe
 
    # 3. Walk up from package location looking for .venv
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        for venv_name in (".venv", "venv"):
            for py_name in ("python3", "python"):
                candidate = parent / venv_name / "bin" / py_name
                if candidate.is_file():
                    return candidate.resolve()
        # Stop at filesystem root or home directory boundary
        if parent == parent.parent:
            break
 
    # Fallback
    return exe
 
 
def _systemd_available() -> bool:
    return shutil.which("systemctl") is not None
 
 
def _service_installed() -> bool:
    return (SYSTEMD_UNIT_DIR / SERVICE_FILENAME).is_file()
 
 
def _service_active() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", SERVICE_FILENAME],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False
 
 
def _has_passwordless_sudo() -> bool:
    if not shutil.which("sudo"):
        return False
    try:
        result = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False
 
 
# ---------------------------------------------------------------------------
# Install helpers
# ---------------------------------------------------------------------------
 
def _run_sudo(*cmd: str) -> tuple[bool, str]:
    """Run a command via sudo. Returns (success, stderr)."""
    try:
        result = subprocess.run(
            ["sudo", *cmd],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0, result.stderr.strip()
    except Exception as exc:
        return False, str(exc)
 
 
def _write_and_enable(service_content: str) -> tuple[bool, str]:
    """Write the unit file to /etc/systemd/system/ and enable it.
 
    Returns (success, error_message).
    """
    unit_path = SYSTEMD_UNIT_DIR / SERVICE_FILENAME
 
    # Write via a temp file then sudo-copy
    tmp = Path("/tmp") / SERVICE_FILENAME
    try:
        tmp.write_text(service_content, encoding="utf-8")
    except OSError as exc:
        return False, f"Could not write temp file: {exc}"
 
    ok, err = _run_sudo("cp", str(tmp), str(unit_path))
    tmp.unlink(missing_ok=True)
    if not ok:
        return False, f"Could not copy unit file: {err}"
 
    ok, err = _run_sudo("chmod", "644", str(unit_path))
    if not ok:
        return False, f"Could not set permissions: {err}"
 
    ok, err = _run_sudo("systemctl", "daemon-reload")
    if not ok:
        return False, f"daemon-reload failed: {err}"
 
    ok, err = _run_sudo("systemctl", "enable", "--now", SERVICE_FILENAME)
    if not ok:
        return False, f"systemctl enable --now failed: {err}"
 
    return True, ""
 
 
def _restart_or_start() -> tuple[bool, str]:
    """Start or restart the service. Returns (success, error_message)."""
    action = "restart" if _service_active() else "start"
    ok, err = _run_sudo("systemctl", action, SERVICE_FILENAME)
    return ok, (f"systemctl {action} failed: {err}" if not ok else "")
 
 
# ---------------------------------------------------------------------------
# Manual command strings
# ---------------------------------------------------------------------------
 
def manual_install_commands(service_content: str, config_path: Path) -> str:
    """Return shell commands the user can copy-paste to install manually."""
    unit_path = SYSTEMD_UNIT_DIR / SERVICE_FILENAME
    return textwrap.dedent(f"""\
        # 1. Write the unit file
        sudo tee {unit_path} << 'EOF'
        {service_content.rstrip()}
        EOF
 
        # 2. Set permissions and reload
        sudo chmod 644 {unit_path}
        sudo systemctl daemon-reload
 
        # 3. Enable and start
        sudo systemctl enable --now {SERVICE_FILENAME}
 
        # 4. Check status
        sudo systemctl status {SERVICE_FILENAME}
    """)
 
 
def manual_restart_commands() -> str:
    return f"sudo systemctl restart {SERVICE_FILENAME}"
 
 
# ---------------------------------------------------------------------------
# Interactive service step — called from installer
# ---------------------------------------------------------------------------
 
def service_step(
    *,
    config_path: Path,
    interactive: bool = True,
) -> None:
    """Offer to install/update the systemd service.
 
    Called at the end of the installer flow. Handles four cases:
    - systemd not present     → skip gracefully
    - service already exists  → offer restart
    - first install           → offer to install automatically or print manual cmds
    - no passwordless sudo    → always print manual commands
 
    Args:
        config_path: The --config-path value (written into ExecStart).
        interactive: If False, skip all prompts and just print commands.
    """
    if not _systemd_available():
        print()
        print("  systemd not detected — skipping service setup.")
        print("  Start manually with:  system-sensors-run")
        return
 
    venv_python     = _venv_python()
    run_as_user     = _current_user()
    service_content = build_service_file(
        run_as_user=run_as_user,
        venv_python=venv_python,
        config_path=config_path,
    )
 
    already_installed = _service_installed()
 
    print()
    print("─" * 56)
    print("  Systemd service")
    print("─" * 56)
 
    # ── Service already installed ────────────────────────────────────────
    if already_installed:
        print(f"  {SERVICE_FILENAME!r} is already installed.")
        if not interactive:
            print()
            print("  To restart after a reprobe, run:")
            print(f"    {manual_restart_commands()}")
            return
 
        if _ask_yn("  Restart the service now?", default=True):
            if _has_passwordless_sudo():
                ok, err = _restart_or_start()
                if ok:
                    print(f"  ✓ Service restarted.")
                else:
                    print(f"  ✗ Restart failed: {err}")
                    print(f"  Run manually:  {manual_restart_commands()}")
            else:
                print("  Run this to restart:")
                print(f"    {manual_restart_commands()}")
        return
 
    # ── First install ────────────────────────────────────────────────────
    print(f"  Unit path : {SYSTEMD_UNIT_DIR / SERVICE_FILENAME}")
    print(f"  Run as    : {run_as_user}")
    print(f"  Python    : {venv_python}")
    print(f"  Config    : {config_path}")
    print()
 
    if not interactive:
        print("  Non-interactive — run these commands to install the service:")
        print()
        _indent(manual_install_commands(service_content, config_path))
        return
 
    if not _ask_yn("  Install and enable as a systemd service?", default=True):
        print()
        print("  Skipped. To install later, run:")
        print()
        _indent(manual_install_commands(service_content, config_path))
        return
 
    if _has_passwordless_sudo():
        print("  Installing...")
        ok, err = _write_and_enable(service_content)
        if ok:
            print(f"  ✓ Installed and started.")
            print(f"  Check status:  sudo systemctl status {SERVICE_FILENAME}")
        else:
            print(f"  ✗ Automatic install failed: {err}")
            print("  Run these commands manually:")
            print()
            _indent(manual_install_commands(service_content, config_path))
    else:
        print("  sudo requires a password — run these commands manually:")
        print()
        _indent(manual_install_commands(service_content, config_path))
 
 
# ---------------------------------------------------------------------------
# Small UI helpers
# ---------------------------------------------------------------------------
 
def _ask_yn(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    while True:
        try:
            raw = input(f"{prompt}{suffix}: ").strip().lower()
        except EOFError:
            return default
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  Please enter y or n.")
 
 
def _indent(text: str, prefix: str = "    ") -> None:
    for line in text.splitlines():
        print(f"{prefix}{line}")