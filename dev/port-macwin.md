# Port plan: Haus.Licht daemon to macOS and Windows

## 1. Module table

| Module | Linux-specific | macOS approach | Windows approach | Verdict |
|---|---|---|---|---|
| haus/daemon.py | Unix socket server; wires Desktop/notifywatch/hci | TCP-localhost + token via shim | TCP-localhost + token or named pipe via shim | shim |
| haus/common.py | XDG_RUNTIME_DIR socket path; fcntl.flock | platformdirs paths; filelock | platformdirs paths; filelock | shim |
| haus/hci.py | AF_BLUETOOTH raw HCI, /sys/class/bluetooth, ioctl HCIGETDEVINFO, BlueZ D-Bus | — | — | Linux-only, disabled elsewhere |
| haus/backends/mesh.py | claims adapter via hci.py; BlueZ ObjectManager; BleakScanner bluez filters | bleak scanner only if btmesh[ble] allows; else n/a | bleak scanner only if btmesh[ble] allows; else n/a | Linux-only core; discovery shim possible |
| haus/desktop.py | /sys/class/backlight, /proc/acpi/button/lid, /sys/class/drm, /dev/uleds, xdg portals, systemctl/busctl | stub lid/backlight/screen; night light off | stub | shim (stub off Linux) |
| haus/kde.py | session bus, Plasma KConfig via dbus-fast | — | — | Linux-only |
| haus/notifywatch.py | org.freedesktop.Notifications D-Bus monitor | — | — | Linux-only, disabled elsewhere |
| haus/videofollow.py | xdg-desktop-portal + gst-launch-1.0 pipewiresrc | mss frame grabber shim | mss frame grabber shim | shim; parity low |
| haus/clap.py, haus/audiofollow.py | pw-record/parec/pactl (PipeWire/Pulse) | sounddevice | sounddevice (WASAPI) | shim |
| haus/backends/sysled.py | /sys/class/leds + logind D-Bus | — | — | Linux-only |
| haus/backends/usblight.py | /dev/bus/usb, udev rule, /dev/hidraw | libusb/hidapi device paths differ | WinUSB/hidapi device paths differ | shim limited / Linux-only |
| haus/backends/serial.py | /dev/serial/by-id udev links | serial.tools.list_ports | serial.tools.list_ports | shim |
| haus/rootsetup.py, haus/disarm.py | pkexec/sudo, install/hci-claim helper, systemd | — | — | Linux-only |
| haus/configwin.py | XDG_CURRENT_DESKTOP, pgrep systemsettings | stub | stub | Linux-only |
| haus/debugview.py | Unix socket client | TCP/named pipe shim | TCP/named pipe shim | shim |
| haus/profiles.py, haus/cli.py | hci/pkexec references | gate behind IS_LINUX | gate behind IS_LINUX | shim (gated) |
| haus/backends/_bulbkit.py | BlueZ D-Bus device properties | — | — | Linux-only |
| LAN backends (wiz, hue, lifx, yeelight, tasmota, esphome, shelly, tuya, govee, dmx, mqtt, kasa, wled, generic, mock) | UDP/TCP stdlib sockets only | as-is | as-is | as-is |

Already cross-platform dependencies: bleak (macOS CoreBluetooth, Windows WinRT), numpy, ai-edge-litert. dbus-fast is effectively Linux-only here (system/session bus assumptions).

## 2. Shims to write (one small module each)

- haus/_platform.py — IS_LINUX / IS_MAC / IS_WIN from sys.platform; single import point for gating.
- haus/paths.py — platformdirs AppDirs("hauslicht") for config/state/cache; replaces XDG_STATE_HOME/XDG_CACHE_HOME reads in haus/common.py, haus/configwin.py, haus/clap.py.
- haus/lock.py — filelock-based replacement for fcntl.flock in haus/common.py (single-instance socket lock).
- haus/ipc.py — Unix socket at the runtime dir on Linux; TCP localhost with a token file on macOS; named pipe or TCP on Windows. Used by haus/daemon.py (server) and haus/debugview.py (client).
- haus/audio_capture.py — sounddevice callback yielding 10 ms s16 16 kHz mono blocks; replaces pw-record/parec+pactl in haus/clap.py and haus/audiofollow.py when those binaries are absent.
- haus/screencap.py — mss frame grabber; optional fallback for haus/videofollow.py when the portal/gstreamer path is unavailable.
- haus/notify_stub.py — no-op notification monitor so haus/notifywatch.py reports "unavailable" instead of crashing off Linux.

Autostart (no new code file): launchd plist in ~/Library/LaunchAgents on macOS; Task Scheduler or Startup-folder shortcut on Windows; keep systemd --user on Linux.

## 3. Order of work (each independently testable)

1. Add platformdirs, filelock, sounddevice, mss to requirements; write haus/_platform.py, haus/paths.py, haus/lock.py; swap XDG/fcntl in haus/common.py, haus/configwin.py, haus/clap.py; gate Linux-only imports. Test: `import haus` and the unit suite pass on Linux/macOS/Windows.
2. Write haus/ipc.py; rework socket binding in haus/daemon.py and the connect path in haus/debugview.py. Test: JSON-lines connect/set/get over loopback on all three OSes, no hardware.
3. Write haus/audio_capture.py (sounddevice); make haus/clap.py and haus/audiofollow.py fall back to it and report "capture unavailable" cleanly. Test: device enumeration + silence handling on macOS/Windows.
4. Gate haus/hci.py, haus/backends/mesh.py exclusive mode, haus/backends/_bulbkit.py behind IS_LINUX; stub mesh discovery with bleak where btmesh allows. Test: daemon starts with no Bluetooth adapter present.
5. Write haus/notify_stub.py; stub haus/desktop.py (no /sys, no D-Bus portals) off Linux; disable notifywatch/desktop/kde/sysled elsewhere. Test: status JSON omits Linux-only keys off Linux.
6. Write haus/screencap.py (mss); haus/videofollow.py uses it only when portal/gstreamer are absent. Test: returns frames or reports "unavailable".
7. haus/backends/serial.py via serial.tools.list_ports; document haus/backends/usblight.py path differences; gate haus/cli.py setup-root to Linux.
8. Freeze: full unit suite plus `hauslicht-ctl status` smoke test on macos-latest/windows-latest/ubuntu-latest; write autostart docs (launchd plist / Task Scheduler / Startup shortcut).

## 4. CI plan

GitHub Actions job `test`, strategy.matrix `os: [ubuntu-latest, macos-latest, windows-latest]`, Python 3.11:
- install: `pip install -r requirements.txt -r requirements-clap.txt pytest pytest-asyncio` (clap model download pinned by sha256 in haus/clap.py; allow failure or pre-fetch the model).
- run: `pytest -q` with `HAUSLICHT_BACKEND=mock`; deselect hardware tests.
- Mocked everywhere: no Bluetooth adapter on runners → mock hci.list_adapters, hci.bluez_adapters, bleak.BleakScanner; no D-Bus session bus → session-bus calls raise or return stubs; no pw-record/parec/pactl or PipeWire → audio path asserts "unavailable"; no xdg-desktop-portal/gst-launch → videofollow asserts "unavailable"; /sys and /proc fixtures already tmp-tree based in haus/backends/usblight.py, haus/backends/serial.py, haus/backends/sysled.py tests.
- macOS/Windows CI only validates "feature unavailable" behavior and cross-platform core (IPC, config paths, LAN backends); real-device mesh/clip/audio parity is out of scope for CI.

## 5. Risks and open questions

- btmesh's exclusive advertising bearer needs raw HCI and a BlueZ-free adapter; bleak cannot claim an adapter from BlueZ → mesh control likely Linux-only on macOS/Windows.
- CI runners have no Bluetooth adapter and no mic/screen permissions → only "unavailable" paths are tested; real clap/audio/video-follow parity unverified.
- sounddevice defaults (device, sample rate, WASAPI exclusive mode, macOS mic permission) differ from the pw-record pipeline → clap thresholds/latency need per-OS retuning.
- mss on macOS requires Screen Recording permission; on Windows it captures all displays → videofollow parity low, may remain Linux-only.
- TCP/named-pipe IPC with a token changes the client contract (hauslicht-ctl, Plasma widget, applets, Qt tray, bar modules); no listed file covers token auth — it must be designed.
- Moving config/state to platformdirs changes default paths on Linux unless XDG_* overrides are explicitly preserved.
- No pkexec/sudo equivalent → rootsetup.py, disarm.py, exclusive_adapter, and `ctl setup-root` are Linux-only; document as such.
- No macOS/Windows hardware or developers in scope → high risk of undetected regressions; gate features behind IS_LINUX by default.

## Review notes (orchestrator, 2026-11-03) — these override the plan above
- The mesh backend's normal mode talks to the bulbs through bleak (GATT proxy, bleak_retry_connector), which is cross-platform. Only exclusive mode (hci.py, raw HCI, root helper) is Linux-only. So mesh control should work on macOS/Windows; verify by reading backends/mesh.py.
- macOS keeps the Unix socket (asyncio.start_unix_server works there); only Windows needs TCP 127.0.0.1 + a token file in %LOCALAPPDATA%\hauslicht. Linux paths and the socket contract stay exactly as they are (no platformdirs on Linux).
- Prefer stdlib over new dependencies where it is cheap (msvcrt.locking instead of filelock; os.environ LOCALAPPDATA / ~/Library/Application Support instead of platformdirs).
- No CI run on macOS/Windows until the user decides about pushing; meanwhile tests simulate non-Linux in a subprocess.

## Step 2 done
TCP transport (haus/ipc.py) on Windows or HAUSLICHT_TRANSPORT=tcp; Unix socket unchanged on Linux.
