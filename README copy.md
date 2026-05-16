# system_sensors

Implementation-agnostic system metrics -> MQTT / Home Assistant discovery publisher.

## Install

```
pip install -e .
system-sensors-install
```

The installer probes the host's capabilities, writes `settings.yaml` (on first
install) and `sensors_enabled.yaml` to `~/.config/system_sensors/`, and prints a
summary of what was detected and what changed since the last run.

## Re-run capability detection

```
system-sensors-install --reprobe
```

User-set `enabled: true/false` flags in `sensors_enabled.yaml` are preserved
across re-runs. Sensors that stop probing True are marked `available: false`
rather than removed. See `~/sensors/PLAN.md` "Merge semantics" for the full
truth table.

## Run

```
system-sensors-run
```

Logs go to stdout. MQTT publish happens every `update_interval` seconds
(default 60). Stop with Ctrl-C.

## Layout

- `src/system_sensors/sensors/` -- one module per logical sensor grouping.
- `src/system_sensors/registry.py` -- discovers Sensor subclasses, probes,
  picks variants.
- `src/system_sensors/config.py` -- YAML I/O and schema validation.
- `src/system_sensors/merge.py` -- pure functions for re-run merge semantics.
- `src/system_sensors/installer.py` -- install-time probe + merge + write.
- `src/system_sensors/runtime.py` -- asyncio publish loop driven by the
  generated `sensors_enabled.yaml`.
