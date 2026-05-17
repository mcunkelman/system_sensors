"""Hardware and OS capability report for system_sensors.

Run standalone to print a human-readable pre-flight report before installing:

    python -m system_sensors.detect
    # or after pip install -e .:
    system-sensors-detect

The report shows everything discovered on this host — including items that
will be filtered or flagged during install — so the user can see the full
picture before committing to a settings.yaml. Filtered items are annotated
with the reason so users can make informed decisions about whether to override.

This module does NOT write any files. It is purely informational.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CpuInfo:
    model: str = "Unknown"
    vendor: str = "Unknown"
    physical_cores: int | None = None
    threads: int | None = None


@dataclass
class GpuInfo:
    vendor: str
    model: str
    source: str
    temp_available: bool = False
    util_available: bool = False
    vram_mb: int | None = None
    driver_version: str | None = None
    filtered: bool = False
    filter_reason: str = ""


@dataclass
class RamInfo:
    total_mb: int | None = None
    swap_total_mb: int | None = None
    swap_present: bool = False


@dataclass
class NetworkIface:
    name: str
    is_up: bool
    is_wifi: bool
    ip: str | None = None
    speed_mbps: int | None = None


@dataclass
class MountPoint:
    mountpoint: str
    fstype: str
    device: str
    optional: bool = False


@dataclass
class ZPool:
    name: str
    capacity_pct: str
    health: str


@dataclass
class CapabilityReport:
    # Identity
    hostname: str = ""
    ip: str = ""
    os: str = ""
    device_model: str = ""
    is_raspberry_pi: bool = False
    arch: str = ""
    python_version: str = ""

    # Dependencies
    psutil_available: bool = False
    rpi_bad_power: bool = False
    apt_available: bool = False
    package_manager: str | None = None

    # Hardware
    cpu_info: CpuInfo = field(default_factory=CpuInfo)
    cpu_temp_source: str | None = None
    cpu_freq_available: bool = False
    gpu_info: list[GpuInfo] = field(default_factory=list)
    fans_available: bool = False
    ram_info: RamInfo = field(default_factory=RamInfo)

    # Network
    network_interfaces: list[NetworkIface] = field(default_factory=list)

    # Storage
    mount_points: list[MountPoint] = field(default_factory=list)
    zpools: list[ZPool] = field(default_factory=list)

    # Warnings accumulated during detection
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_file(path: str | Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _run_sync(argv: list[str], timeout: float = 5.0) -> str | None:
    """Run a subprocess synchronously. Returns stripped stdout or None."""
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def _detect_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def _detect_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def _detect_device_model() -> str:
    for path in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        val = _read_file(path)
        if val:
            return val.rstrip("\x00")
    return platform.machine() or "unknown"


def _detect_os() -> str:
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        content = _read_file(path)
        if not content:
            continue
        for line in content.splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    return platform.platform()


def _detect_package_manager() -> str | None:
    for name, path in [("apt", "/usr/bin/apt"), ("dnf", "/usr/bin/dnf"),
                        ("pacman", "/usr/bin/pacman"), ("zypper", "/usr/bin/zypper")]:
        if Path(path).is_file():
            return name
    return None


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------

def _detect_cpu_info() -> CpuInfo:
    info = CpuInfo()
    content = _read_file("/proc/cpuinfo")
    if not content:
        return info

    info.threads = len(re.findall(r"^processor\s*:", content, re.MULTILINE))

    for line in content.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "vendor_id":
            info.vendor = val
        elif key == "model name" and info.model == "Unknown":
            info.model = val
        elif key == "cpu part" and info.model == "Unknown":
            info.model = f"ARM CPU part {val}"
        elif key == "hardware" and info.model == "Unknown":
            info.model = val

    # Physical cores via sysfs
    cpu_dir = Path("/sys/devices/system/cpu")
    if cpu_dir.is_dir():
        core_ids: set[str] = set()
        for cpu in cpu_dir.iterdir():
            if not re.match(r"^cpu\d+$", cpu.name):
                continue
            core_id = _read_file(cpu / "topology" / "core_id")
            if core_id:
                core_ids.add(core_id)
        if core_ids:
            info.physical_cores = len(core_ids)

    return info


def _detect_cpu_temp_source() -> str | None:
    cpu_keywords = ["cpu", "soc", "core", "k10temp", "coretemp",
                    "cpu-thermal", "cpu_thermal", "soc_thermal"]
    try:
        import psutil
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            for zone in temps:
                if any(kw in zone.lower() for kw in cpu_keywords):
                    return f"{zone} (psutil)"
    except Exception:
        pass

    thermal = Path("/sys/class/thermal")
    if thermal.is_dir():
        for zone in sorted(thermal.glob("thermal_zone*")):
            zone_type = _read_file(zone / "type") or ""
            if any(kw in zone_type.lower() for kw in cpu_keywords):
                return f"{zone.name}/{zone_type} (sysfs)"
    return None


def _detect_cpu_freq() -> bool:
    try:
        import psutil
        return psutil.cpu_freq() is not None
    except Exception:
        pass
    return Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq").is_file()


def _detect_fans() -> bool:
    try:
        import psutil
        if hasattr(psutil, "sensors_fans"):
            return bool(psutil.sensors_fans())
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------

_PCI_VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel"}

_FRAMEBUFFER_KEYWORDS = ("simple-framebuffer", "framebuffer", "vesa",
                         "efifb", "bochs")


def _is_framebuffer(model: str) -> bool:
    return any(kw in model.lower() for kw in _FRAMEBUFFER_KEYWORDS)


def _detect_drm_gpus() -> list[GpuInfo]:
    gpus: list[GpuInfo] = []
    drm = Path("/sys/class/drm")
    if not drm.is_dir():
        return gpus

    seen: set[str] = set()
    for card in sorted(drm.iterdir()):
        if not re.match(r"^card\d+$", card.name):
            continue
        dev = card / "device"
        if not dev.is_dir():
            continue
        real = str(Path(dev).resolve())
        if real in seen:
            continue
        seen.add(real)

        vendor_id = _read_file(dev / "vendor") or "unknown"
        vendor = _PCI_VENDORS.get(vendor_id, vendor_id)

        model = _read_file(dev / "label")
        if not model:
            uevent = _read_file(dev / "uevent") or ""
            for line in uevent.splitlines():
                if line.startswith("DRIVER="):
                    model = line.split("=", 1)[1]
                    break
        if not model:
            device_id = _read_file(dev / "device")
            model = f"{vendor} GPU ({vendor_id}:{device_id})" if device_id else f"{vendor} GPU"

        temp_zone = None
        hwmon = dev / "hwmon"
        if hwmon.is_dir():
            for h in hwmon.iterdir():
                name = _read_file(h / "name")
                if name:
                    temp_zone = name
                    break

        filtered = _is_framebuffer(model)
        gpu = GpuInfo(
            vendor=vendor,
            model=model,
            source="drm/sysfs",
            temp_available=temp_zone is not None,
            util_available=False,
            filtered=filtered,
            filter_reason="EFI/VESA framebuffer — not a real GPU, no metrics available" if filtered else "",
        )
        gpus.append(gpu)
    return gpus


def _detect_nvidia_gpus() -> list[GpuInfo]:
    if not shutil.which("nvidia-smi"):
        return []
    out = _run_sync(
        ["nvidia-smi", "--query-gpu=name,uuid,driver_version,memory.total",
         "--format=csv,noheader,nounits"]
    )
    if not out:
        return []
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if not parts:
            continue
        vram = None
        try:
            vram = int(parts[3]) if len(parts) > 3 else None
        except (ValueError, IndexError):
            pass
        gpus.append(GpuInfo(
            vendor="NVIDIA",
            model=parts[0] if parts else "Unknown",
            source="nvidia-smi",
            temp_available=True,
            util_available=True,
            vram_mb=vram,
            driver_version=parts[2] if len(parts) > 2 else None,
        ))
    return gpus


def _detect_videocore_gpu() -> list[GpuInfo]:
    if not shutil.which("vcgencmd"):
        return []
    out = _run_sync(["vcgencmd", "measure_temp"])
    if out and "temp=" in out:
        return [GpuInfo(vendor="Broadcom", model="VideoCore GPU",
                        source="vcgencmd", temp_available=True)]
    return []


def _detect_all_gpus() -> list[GpuInfo]:
    nvidia = _detect_nvidia_gpus()
    videocore = _detect_videocore_gpu()
    drm = _detect_drm_gpus()
    # Drop DRM entries for NVIDIA if nvidia-smi found them (avoid duplicates)
    if nvidia:
        drm = [g for g in drm if g.vendor != "NVIDIA"]
    return nvidia + drm + videocore


# ---------------------------------------------------------------------------
# RAM
# ---------------------------------------------------------------------------

def _detect_ram() -> RamInfo:
    info = RamInfo()
    try:
        import psutil
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        info.total_mb = round(mem.total / 1024 / 1024)
        info.swap_total_mb = round(swap.total / 1024 / 1024)
        info.swap_present = swap.total > 0
        return info
    except Exception:
        pass
    content = _read_file("/proc/meminfo")
    if content:
        for line in content.splitlines():
            if line.startswith("MemTotal:"):
                info.total_mb = int(line.split()[1]) // 1024
            elif line.startswith("SwapTotal:"):
                kb = int(line.split()[1])
                info.swap_total_mb = kb // 1024
                info.swap_present = kb > 0
    return info


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

_IGNORED_IFACE_PREFIXES = (
    "lo", "veth", "docker", "virbr", "br-",
    "tun", "tap", "dummy", "bond", "team",
)


def _wifi_interfaces() -> set[str]:
    net = Path("/sys/class/net")
    if not net.is_dir():
        return set()
    return {iface.name for iface in net.iterdir()
            if (iface / "wireless").is_dir()}


def _detect_network_interfaces() -> list[NetworkIface]:
    wifi = _wifi_interfaces()
    found: list[NetworkIface] = []
    try:
        import psutil, socket as _socket
        for iface, stat in psutil.net_if_stats().items():
            if any(iface.startswith(p) for p in _IGNORED_IFACE_PREFIXES):
                continue
            ip = None
            for addr in psutil.net_if_addrs().get(iface, []):
                if addr.family == _socket.AF_INET:
                    ip = addr.address
                    break
            found.append(NetworkIface(
                name=iface, is_up=stat.isup, is_wifi=iface in wifi,
                ip=ip, speed_mbps=stat.speed or None,
            ))
        return found
    except Exception:
        pass
    net = Path("/sys/class/net")
    if net.is_dir():
        for iface in net.iterdir():
            if any(iface.name.startswith(p) for p in _IGNORED_IFACE_PREFIXES):
                continue
            operstate = _read_file(iface / "operstate") or ""
            found.append(NetworkIface(
                name=iface.name, is_up=operstate == "up",
                is_wifi=iface.name in wifi,
            ))
    return found


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_IGNORED_FS_TYPES = {
    "tmpfs", "devtmpfs", "sysfs", "proc", "devpts", "cgroup", "cgroup2",
    "pstore", "bpf", "tracefs", "securityfs", "mqueue", "hugetlbfs",
    "fusectl", "overlay", "squashfs", "autofs", "ramfs", "configfs",
    "efivarfs", "debugfs", "rpc_pipefs",
}

_IGNORED_MOUNT_PREFIXES = (
    "/sys", "/proc", "/dev", "/run", "/snap", "/var/snap",
)

_OPTIONAL_MOUNTS = ("/boot", "/boot/efi")


def _detect_mount_points() -> list[MountPoint]:
    found: list[MountPoint] = []
    seen: set[str] = set()

    def _add(mp: str, fstype: str, device: str) -> None:
        if mp in seen or fstype.lower() in _IGNORED_FS_TYPES:
            return
        if any(mp.startswith(p) for p in _IGNORED_MOUNT_PREFIXES):
            return
        seen.add(mp)
        found.append(MountPoint(
            mountpoint=mp, fstype=fstype, device=device,
            optional=any(mp.startswith(p) for p in _OPTIONAL_MOUNTS),
        ))

    try:
        import psutil
        for part in psutil.disk_partitions(all=True):
            _add(part.mountpoint, part.fstype, part.device)
        return found
    except Exception:
        pass
    content = _read_file("/proc/mounts")
    if content:
        for line in content.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                _add(parts[1], parts[2], parts[0])
    return found


def _detect_zpools() -> list[ZPool]:
    zpool_bin = shutil.which("zpool") or next(
        (p for p in ["/usr/sbin/zpool", "/sbin/zpool"] if Path(p).is_file()), None
    )
    if not zpool_bin:
        return []
    out = _run_sync([zpool_bin, "list", "-H", "-o", "name,capacity,health"])
    if not out:
        return []
    pools = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            pools.append(ZPool(
                name=parts[0].strip(),
                capacity_pct=parts[1].strip().rstrip("%"),
                health=parts[2].strip(),
            ))
    return pools


# ---------------------------------------------------------------------------
# Optional dependency probes
# ---------------------------------------------------------------------------

def _probe_psutil() -> bool:
    try:
        import psutil  # noqa: F401
        return True
    except ImportError:
        return False


def _probe_rpi_bad_power() -> bool:
    try:
        from rpi_bad_power import new_under_voltage
        return new_under_voltage() is not None
    except Exception:
        return False


def _probe_apt() -> bool:
    try:
        import apt  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_report() -> CapabilityReport:
    """Probe the current system and return a CapabilityReport.

    All detection is best-effort — failures are logged to report.warnings
    rather than raised. The report is purely informational; nothing is written.
    """
    r = CapabilityReport()

    r.hostname        = _detect_hostname()
    r.ip              = _detect_ip()
    r.os              = _detect_os()
    r.device_model    = _detect_device_model()
    r.is_raspberry_pi = "raspberry pi" in r.device_model.lower()
    r.arch            = platform.machine()
    r.python_version  = platform.python_version()

    r.psutil_available = _probe_psutil()
    r.rpi_bad_power    = _probe_rpi_bad_power()
    r.apt_available    = _probe_apt()
    r.package_manager  = _detect_package_manager()

    r.cpu_info          = _detect_cpu_info()
    r.cpu_temp_source   = _detect_cpu_temp_source()
    r.cpu_freq_available = _detect_cpu_freq()
    r.fans_available    = _detect_fans()
    r.gpu_info          = _detect_all_gpus()
    r.ram_info          = _detect_ram()
    r.network_interfaces = _detect_network_interfaces()
    r.mount_points      = _detect_mount_points()
    r.zpools            = _detect_zpools()

    # Collect warnings
    if not r.psutil_available:
        r.warnings.append("psutil not installed — run: pip install psutil")
    if not r.cpu_temp_source:
        r.warnings.append("No CPU temperature source found")
    if not r.network_interfaces:
        r.warnings.append("No network interfaces detected")
    if not r.mount_points:
        r.warnings.append("No storage mount points detected")
    for gpu in r.gpu_info:
        if gpu.filtered:
            r.warnings.append(
                f"GPU '{gpu.model}' will be filtered: {gpu.filter_reason}"
            )

    return r


# ---------------------------------------------------------------------------
# Human-readable report
# ---------------------------------------------------------------------------

def _mb_human(mb: int | None) -> str:
    if mb is None:
        return "unknown"
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb} MB"


def _yn(val: bool) -> str:
    return "yes" if val else "NO (missing)"


def print_report(r: CapabilityReport) -> None:
    sep = "-" * 58
    print(sep)
    print("  system_sensors — pre-flight capability report")
    print(sep)

    print("\n[Device]")
    print(f"  Hostname      : {r.hostname}")
    print(f"  IP address    : {r.ip}")
    print(f"  Model         : {r.device_model}")
    print(f"  Architecture  : {r.arch}")
    print(f"  OS            : {r.os}")
    print(f"  Python        : {r.python_version}")
    print(f"  Raspberry Pi  : {'yes' if r.is_raspberry_pi else 'no'}")

    print("\n[CPU]")
    print(f"  Model         : {r.cpu_info.model}")
    print(f"  Vendor        : {r.cpu_info.vendor}")
    cores   = r.cpu_info.physical_cores
    threads = r.cpu_info.threads
    print(f"  Cores/Threads : {cores or '?'} cores / {threads or '?'} threads")
    print(f"  Temperature   : {r.cpu_temp_source or 'not detected'}")
    print(f"  Frequency     : {_yn(r.cpu_freq_available)}")
    print(f"  Fans          : {'detected' if r.fans_available else 'none detected'}")

    print("\n[GPU]")
    real_gpus    = [g for g in r.gpu_info if not g.filtered]
    filtered_gpus = [g for g in r.gpu_info if g.filtered]
    if real_gpus:
        for gpu in real_gpus:
            print(f"  {gpu.vendor:<10} {gpu.model}")
            print(f"             source     : {gpu.source}")
            if gpu.vram_mb is not None:
                print(f"             VRAM       : {_mb_human(gpu.vram_mb)}")
            if gpu.driver_version:
                print(f"             driver     : {gpu.driver_version}")
            print(f"             temp       : {'yes' if gpu.temp_available else 'no'}"
                  f"  |  utilization: {'yes' if gpu.util_available else 'no'}")
    else:
        print("  None detected")
    if filtered_gpus:
        print("  Filtered (will not become sensors):")
        for gpu in filtered_gpus:
            print(f"    {gpu.model}")
            print(f"      reason: {gpu.filter_reason}")
            print(f"      override: set filter_framebuffer: false in settings.yaml")

    print("\n[RAM]")
    print(f"  Total         : {_mb_human(r.ram_info.total_mb)}")
    swap = "present" if r.ram_info.swap_present else "none"
    print(f"  Swap          : {_mb_human(r.ram_info.swap_total_mb)} ({swap})")

    print("\n[Network interfaces]")
    if r.network_interfaces:
        for iface in r.network_interfaces:
            status = "UP  " if iface.is_up else "DOWN"
            kind   = "wifi" if iface.is_wifi else "eth "
            ip     = iface.ip or ""
            speed  = f"  {iface.speed_mbps} Mbps" if iface.speed_mbps else ""
            print(f"  [{status}] {kind}  {iface.name:<14} {ip:<18}{speed}")
    else:
        print("  None detected")

    print("\n[Storage mount points]")
    if r.mount_points:
        for mp in r.mount_points:
            tag = "  (optional — disabled by default)" if mp.optional else ""
            print(f"  {mp.mountpoint:<30} [{mp.fstype}]  {mp.device}{tag}")
    else:
        print("  None detected")

    print("\n[ZFS pools]")
    if r.zpools:
        for pool in r.zpools:
            print(f"  {pool.name:<22} {pool.capacity_pct:>4}% used  health={pool.health}")
    else:
        print("  None detected (zpool not installed or no pools configured)")

    print("\n[Dependencies]")
    print(f"  psutil        : {_yn(r.psutil_available)}")
    print(f"  rpi-bad-power : {_yn(r.rpi_bad_power)}"
          + ("" if r.is_raspberry_pi else "  (Pi-only, expected)"))
    print(f"  python3-apt   : {_yn(r.apt_available)}")
    print(f"  Pkg manager   : {r.package_manager or 'none detected'}")

    if r.warnings:
        print("\n[Warnings]")
        for w in r.warnings:
            print(f"  ! {w}")

    print()


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: print the capability report and exit."""
    report = build_report()
    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
