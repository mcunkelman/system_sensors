# system_sensors

An implementation-agnostic fork of [Sennevds/system_sensors](https://github.com/Sennevds/system_sensors).

Publishes host metrics to an MQTT broker with Home Assistant auto-discovery.
The same install command works on a Raspberry Pi, an x86 server, or any other
Linux host — the installer probes the hardware and enables only the sensors
that actually exist on that machine.

---

## What it does

- Probes CPU, GPU, RAM, storage, network, and OS at install time
- Writes a config file containing only the sensors found on this host
- Publishes sensor readings to MQTT every 60 seconds (configurable)
- Uses Home Assistant MQTT discovery — entities appear automatically, no
  `configuration.yaml` edits needed
- Runs as a systemd service that starts on boot

---

## Supported hardware and sensors

| Category | Sensors |
|---|---|
| CPU | Usage %, temperature, clock speed, load average (1m/5m/15m) |
| GPU — NVIDIA | Temperature, utilization %, memory used, power draw, fan speed |
| GPU — AMD | Temperature, junction temp, memory temp, fan speed, power draw |
| GPU — Intel | Temperature, power draw |
| GPU — Pi VideoCore | Temperature |
| RAM | Usage %, swap usage % |
| Network | TX/RX throughput per interface, WiFi signal strength, SSID |
| Storage | Disk usage % per mount point |
| System | Hostname, IP address, OS, architecture, last boot time |
| Updates | Pending package updates (apt / dnf / pacman) |
| Raspberry Pi | Under-voltage / bad power supply detection |

Sensors that are not present on the host are automatically excluded — no
manual configuration needed.

---

## Requirements

- Linux (Raspberry Pi OS, Ubuntu, Debian, Fedora, Arch, or similar)
- Python 3.11 or newer
- An MQTT broker reachable on the local network
- Home Assistant with the MQTT integration enabled (optional but recommended)

---

## Install

```bash
git clone https://github.com/mcunkelman/system_sensors.git
cd system_sensors
python3 bootstrap.py
```

The bootstrap script will:

1. Check your Python version
2. Create a virtual environment (default: `system_sensors/.venv`)
3. Install the package and its dependencies
4. Run the interactive installer, which:
   - Probes your hardware and shows a capability report
   - Collects your MQTT broker settings
   - Writes `settings.yaml` and `sensors_enabled.yaml` to your config directory
   - Offers to install and enable a systemd service

You will be prompted for:
- MQTT broker hostname/IP
- MQTT username and password (if your broker requires authentication)
- Device name (used as the entity prefix in Home Assistant)
- Timezone
- Update interval in seconds (default: 60)

Everything else is detected automatically.

---

## Config files

Two files are written to `~/.config/system_sensors/` by default:

| File | Owned by | Purpose |
|---|---|---|
| `settings.yaml` | You | MQTT credentials, device name, timezone, update interval |
| `sensors_enabled.yaml` | Installer | Generated list of enabled sensors — re-created on each reprobe |

Edit `settings.yaml` freely. Do not hand-edit `sensors_enabled.yaml` — it is
overwritten by the installer on every run.

To use a custom config directory:

```bash
python3 bootstrap.py  # prompts for config path during setup
```

Or pass it explicitly on re-runs:

```bash
.venv/bin/system-sensors-install --config-path /path/to/config
```

---

## Re-running after hardware changes

If you add a GPU, plug in an external drive, or change your MQTT settings:

```bash
.venv/bin/system-sensors-install
```

The installer re-probes the hardware, merges the results with your existing
config (preserving any sensors you manually disabled), and offers to restart
the service.

---

## Useful commands

```bash
# Pre-flight hardware report (no files written)
.venv/bin/system-sensors-detect

# Re-run installer / reprobe
.venv/bin/system-sensors-install

# Start the publisher manually (if not using systemd)
.venv/bin/system-sensors-run

# Check service status
sudo systemctl status system_sensors

# View live logs
sudo journalctl -u system_sensors -f
```

---

## Home Assistant

Once the publisher is running, entities appear automatically in Home Assistant
under the device name you chose during setup. No `configuration.yaml` changes
are needed.

If entities do not appear:
1. Check that the MQTT integration is configured in Home Assistant
2. Verify the broker hostname and credentials in `settings.yaml`
3. Run `system-sensors-detect` and check for warnings
4. Check the service logs: `sudo journalctl -u system_sensors -f`

---

## Updating

```bash
cd system_sensors
git pull
.venv/bin/pip install -e .
.venv/bin/system-sensors-install
```

---

## Project layout

```
system_sensors/
├── bootstrap.py                  ← run this first on a new device
├── pyproject.toml
├── src/system_sensors/
│   ├── detect.py                 ← hardware probe and capability report
│   ├── installer.py              ← interactive install / reprobe flow
│   ├── runtime.py                ← asyncio publish loop
│   ├── registry.py               ← sensor discovery and variant selection
│   ├── config.py                 ← YAML config I/O
│   ├── merge.py                  ← reprobe merge semantics
│   ├── ha_discovery.py           ← Home Assistant MQTT discovery payloads
│   ├── mqtt_publisher.py         ← MQTT connection and publish logic
│   ├── service.py                ← systemd service generation and install
│   └── sensors/
│       ├── base.py               ← Sensor ABC
│       ├── cpu.py
│       ├── memory.py
│       ├── disk.py
│       ├── network.py
│       ├── wifi.py
│       ├── gpu_nvidia.py
│       ├── gpu_amd.py
│       ├── gpu_intel.py
│       ├── os_info.py
│       ├── os_updates.py
│       └── rpi.py
├── tests/
└── legacy/                       ← original upstream script (reference only)
```

---

## Acknowledgements

Based on [Sennevds/system_sensors](https://github.com/Sennevds/system_sensors).
Original concept and MQTT/HA discovery implementation by Sennevds and contributors.
