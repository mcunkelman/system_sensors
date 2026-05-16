"""Tests for system_sensors.detect."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from system_sensors.detect import (
    CapabilityReport,
    GpuInfo,
    MountPoint,
    NetworkIface,
    ZPool,
    _is_framebuffer,
    build_report,
    print_report,
)


# ---------------------------------------------------------------------------
# _is_framebuffer
# ---------------------------------------------------------------------------

def test_is_framebuffer_matches_simple_framebuffer() -> None:
    assert _is_framebuffer("simple-framebuffer") is True


def test_is_framebuffer_matches_efifb() -> None:
    assert _is_framebuffer("efifb") is True


def test_is_framebuffer_matches_bochs() -> None:
    assert _is_framebuffer("bochs") is True


def test_is_framebuffer_does_not_match_real_gpu() -> None:
    assert _is_framebuffer("Quadro M2000") is False
    assert _is_framebuffer("AMD Radeon RX 6700") is False
    assert _is_framebuffer("Intel Arc A770") is False


# ---------------------------------------------------------------------------
# build_report — smoke test: runs on this host without raising
# ---------------------------------------------------------------------------

def test_build_report_returns_capability_report() -> None:
    report = build_report()
    assert isinstance(report, CapabilityReport)


def test_build_report_hostname_non_empty() -> None:
    report = build_report()
    assert report.hostname != ""
    assert report.hostname != "unknown"


def test_build_report_arch_non_empty() -> None:
    report = build_report()
    assert report.arch != ""


def test_build_report_os_non_empty() -> None:
    report = build_report()
    assert report.os != ""


def test_build_report_python_version_non_empty() -> None:
    report = build_report()
    assert report.python_version != ""


def test_build_report_psutil_available_on_host() -> None:
    report = build_report()
    # psutil is a hard dep; it must be present in the test environment
    assert report.psutil_available is True


def test_build_report_cpu_model_non_empty() -> None:
    report = build_report()
    assert report.cpu_info.model != ""


def test_build_report_ram_total_positive() -> None:
    report = build_report()
    assert report.ram_info.total_mb is not None
    assert report.ram_info.total_mb > 0


def test_build_report_network_interfaces_non_empty() -> None:
    report = build_report()
    assert len(report.network_interfaces) > 0


def test_build_report_mount_points_includes_root() -> None:
    report = build_report()
    mps = [mp.mountpoint for mp in report.mount_points]
    assert "/" in mps


def test_build_report_no_loopback_in_interfaces() -> None:
    report = build_report()
    names = [i.name for i in report.network_interfaces]
    assert "lo" not in names


def test_build_report_no_snap_mounts() -> None:
    report = build_report()
    for mp in report.mount_points:
        assert not mp.mountpoint.startswith("/snap")
        assert not mp.mountpoint.startswith("/var/snap")


def test_build_report_framebuffer_gpus_are_filtered() -> None:
    report = build_report()
    for gpu in report.gpu_info:
        if gpu.filtered:
            assert gpu.filter_reason != ""


def test_build_report_warnings_is_list() -> None:
    report = build_report()
    assert isinstance(report.warnings, list)


# ---------------------------------------------------------------------------
# print_report — smoke test: runs without raising, produces output
# ---------------------------------------------------------------------------

def test_print_report_produces_output(capsys) -> None:
    report = build_report()
    print_report(report)
    captured = capsys.readouterr()
    assert "capability report" in captured.out
    assert report.hostname in captured.out


def test_print_report_shows_warnings(capsys) -> None:
    report = CapabilityReport(
        hostname="testhost",
        warnings=["test warning one", "test warning two"],
    )
    print_report(report)
    captured = capsys.readouterr()
    assert "test warning one" in captured.out
    assert "test warning two" in captured.out


def test_print_report_shows_filtered_gpu(capsys) -> None:
    report = CapabilityReport(
        hostname="testhost",
        gpu_info=[
            GpuInfo(
                vendor="Unknown",
                model="simple-framebuffer",
                source="drm/sysfs",
                filtered=True,
                filter_reason="EFI/VESA framebuffer — not a real GPU",
            )
        ],
    )
    print_report(report)
    captured = capsys.readouterr()
    assert "simple-framebuffer" in captured.out
    assert "Filtered" in captured.out


def test_print_report_shows_zpool(capsys) -> None:
    report = CapabilityReport(
        hostname="testhost",
        zpools=[ZPool(name="tank", capacity_pct="42", health="ONLINE")],
    )
    print_report(report)
    captured = capsys.readouterr()
    assert "tank" in captured.out
    assert "ONLINE" in captured.out


def test_print_report_no_gpus(capsys) -> None:
    report = CapabilityReport(hostname="testhost", gpu_info=[])
    print_report(report)
    captured = capsys.readouterr()
    assert "None detected" in captured.out


def test_print_report_rpi_label_false_on_x86(capsys) -> None:
    report = build_report()
    print_report(report)
    captured = capsys.readouterr()
    if not report.is_raspberry_pi:
        assert "Pi-only, expected" in captured.out
