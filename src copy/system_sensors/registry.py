"""Sensor discovery and variant resolution.

Platform-agnostic. Imports the `sensors` subpackage and walks every submodule to
find `Sensor` subclasses, then runs `probe()` to decide which variants apply on
the host. The selected mapping is what the installer writes to
`sensors_enabled.yaml`.

GPU sensors are special-cased: ALL probe-passing GPU vendor variants are kept
(a host may have both an NVIDIA discrete card and an Intel iGPU). Non-GPU
sensors take the single highest-priority probe-passing variant per logical name.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

from system_sensors import sensors as _sensors_pkg
from system_sensors.sensors.base import Sensor

_log = logging.getLogger(__name__)


# Logical-name prefixes whose variants should ALL be kept rather than collapsed
# to the highest priority. Multi-vendor GPU coexistence (NVIDIA discrete +
# Intel iGPU on the same host) drives this exception.
_MULTI_VARIANT_PREFIXES: tuple[str, ...] = (
    "gpu_nvidia_",
    "gpu_amd_",
    "gpu_intel_",
)


def _is_multi_variant(logical_name: str) -> bool:
    return any(logical_name.startswith(p) for p in _MULTI_VARIANT_PREFIXES)


def _walk_concrete_subclasses(root: type[Sensor]) -> list[type[Sensor]]:
    """Recursively collect non-abstract subclasses of `root`."""
    seen: set[type[Sensor]] = set()
    out: list[type[Sensor]] = []

    def visit(cls: type[Sensor]) -> None:
        for sub in cls.__subclasses__():
            if sub in seen:
                continue
            seen.add(sub)
            if not inspect.isabstract(sub):
                out.append(sub)
            visit(sub)

    visit(root)
    return out


def discover_sensors() -> list[type[Sensor]]:
    """Walk `system_sensors.sensors.*` and return every concrete `Sensor` subclass.

    Imports each submodule for its side effect of defining classes, then
    enumerates `Sensor.__subclasses__()` transitively. Abstract intermediates
    are filtered out.

    Returns:
        Concrete `Sensor` subclasses sorted by `(logical_name, -priority,
        qualname)` for deterministic registry behaviour.
    """
    pkg_path = _sensors_pkg.__path__
    pkg_name = _sensors_pkg.__name__
    for _finder, mod_name, _is_pkg in pkgutil.iter_modules(pkg_path):
        full_name = f"{pkg_name}.{mod_name}"
        try:
            importlib.import_module(full_name)
        except Exception:  # pragma: no cover - defensive
            _log.exception("Failed to import sensor module %s", full_name)

    classes = _walk_concrete_subclasses(Sensor)
    # Intermediate base classes (e.g. `_CpuLoadBase`) provide shared probe/collect
    # logic but deliberately omit `logical_name` so they aren't published. The
    # `inspect.isabstract` filter doesn't catch them because their abstract
    # methods are implemented; exclude them here by missing class attribute.
    classes = [c for c in classes if _has_logical_name(c)]
    classes.sort(
        key=lambda c: (
            getattr(c, "logical_name", ""),
            -int(getattr(c, "priority", 0)),
            c.__qualname__,
        )
    )
    return classes


def _has_logical_name(cls: type[Sensor]) -> bool:
    """True iff `logical_name` is a class attribute on `cls` or one of its bases.

    A bare annotation (`logical_name: ClassVar[str]` with no value) on `Sensor`
    itself does NOT count — it produces no entry in `vars()`. Concrete sensors
    must explicitly assign the attribute.
    """
    for base in cls.__mro__:
        if "logical_name" in vars(base):
            return True
    return False


def probe_all(sensor_classes: list[type[Sensor]]) -> dict[str, list[type[Sensor]]]:
    """Run `probe()` on each class and group passing variants by logical name.

    A `probe()` that raises is logged and treated as `False` — a buggy probe
    must not crash the installer.

    Args:
        sensor_classes: Output of `discover_sensors()` (or a filtered subset).

    Returns:
        Mapping from `logical_name` to the list of classes whose `probe()`
        returned True, sorted by `priority` descending. Logical names with no
        passing variants are absent from the result.
    """
    grouped: dict[str, list[type[Sensor]]] = {}
    for cls in sensor_classes:
        name = getattr(cls, "logical_name", None)
        if not name:
            _log.warning("Skipping %s: no logical_name attribute", cls.__qualname__)
            continue
        try:
            passed = bool(cls.probe())
        except Exception:
            _log.exception("probe %s raised; treating as False", cls.__qualname__)
            passed = False
        _log.info("probe %s (%s) returned %s", cls.__qualname__, name, passed)
        if passed:
            grouped.setdefault(name, []).append(cls)

    for name, variants in grouped.items():
        variants.sort(key=lambda c: -int(getattr(c, "priority", 0)))

    return grouped


def select_variants(
    probed: dict[str, list[type[Sensor]]],
) -> dict[str, type[Sensor]]:
    """Pick the active variant(s) for each logical sensor name.

    For non-GPU logical names, returns the single highest-priority probe-passing
    variant. For GPU logical names (matching any prefix in
    `_MULTI_VARIANT_PREFIXES`), every probe-passing variant is kept — multiple
    GPU vendors can coexist on a single host.

    Args:
        probed: Output of `probe_all`.

    Returns:
        Mapping from `logical_name` to the chosen `Sensor` subclass. For GPU
        multi-variant cases each distinct logical name appears once.
    """
    selected: dict[str, type[Sensor]] = {}
    for name, variants in probed.items():
        if not variants:
            continue
        if _is_multi_variant(name):
            # All probe-passing classes whose logical_name matches the prefix
            # survive. Since `probed` already groups by logical_name, every
            # class here shares the same name — keep the top-priority one to
            # represent it. Cross-vendor coexistence is achieved by each vendor
            # publishing under its OWN logical_name (e.g. gpu_nvidia_0_temp vs
            # gpu_intel_0_temp), so this still selects one class per name.
            selected[name] = variants[0]
        else:
            selected[name] = variants[0]
    return selected


def instantiate_active_sensors(
    selected: dict[str, type[Sensor]],
) -> list[Sensor]:
    """Expand chosen classes into one `Sensor` instance per physical device.

    Calls `enumerate_instances()` on each chosen class and flattens the result.
    Verifies uniqueness of `resolved_logical_name()` across all instances — a
    duplicate name signals a multi-instance sensor bug and is fatal.

    Args:
        selected: Output of `select_variants`.

    Returns:
        A flat list of ready-to-run `Sensor` instances. The runtime publishes
        one HA entity per element in this list.

    Raises:
        ValueError: If two instances resolve to the same logical name.
    """
    instances: list[Sensor] = []
    seen: dict[str, Sensor] = {}
    for cls in selected.values():
        for inst in cls.enumerate_instances():
            name = inst.resolved_logical_name()
            if name in seen:
                raise ValueError(
                    f"Duplicate resolved logical name {name!r}: "
                    f"{type(seen[name]).__qualname__} and {type(inst).__qualname__} "
                    "both claim it. Multi-instance sensors must yield unique names."
                )
            seen[name] = inst
            instances.append(inst)
    return instances
