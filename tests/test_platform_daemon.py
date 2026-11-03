"""The daemon off Linux: `hauslicht sync` starts, serves the ctl protocol and
leaves every Linux-only part alone (desktop, KDE, notification watcher, raw
HCI, root setup). Each run is a subprocess with mock backends, its own state,
socket, HOME and XDG dirs and no session bus. Tripwires in the subprocess make
any use of a Linux-only part fail the run.

macOS: sys.platform faked as darwin (as in test_platform_import). Windows: a
faked win32 would make asyncio pick its proactor loop, so the run stays on
Linux with the gate (haus._platform.IS_LINUX) switched off and the TCP
transport of haus.ipc, token included."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from haus import ipc

REPO = Path(__file__).resolve().parent.parent

FAKE_DARWIN = '''
import sys, types
sys.platform = "darwin"
import socket
if hasattr(socket, "AF_BLUETOOTH"):
    del socket.AF_BLUETOOTH
sys.modules["_scproxy"] = types.ModuleType("_scproxy")
sys.modules["_scproxy"]._get_proxy_settings = lambda: ({},)
sys.modules["_scproxy"]._get_proxies = lambda: {}
'''

GATE_OFF = '''
from haus import _platform
_platform.IS_LINUX = False
'''

TRIPWIRES = '''
from haus import desktop, hci, kde, notifywatch, rootsetup

def linux_only(name):
    def trip(*args, **kwargs):
        raise AssertionError(f"Linux-only {name} used off Linux")
    return trip

desktop.Desktop.__init__ = linux_only("Desktop")
desktop.KbdSlider.open = linux_only("KbdSlider")
kde.Kde.__init__ = linux_only("Kde")
notifywatch.NotifyWatcher.__init__ = linux_only("NotifyWatcher")
hci.scan_sysfs = linux_only("hci.scan_sysfs")
hci.bluez_adapters = linux_only("hci.bluez_adapters")
rootsetup.installed = linux_only("rootsetup.installed")
rootsetup.run = linux_only("rootsetup.run")
'''

ENTRY = '''
from haus import cli
raise SystemExit(cli.main(["sync"]))
'''


def ask(sock: Path, cmd: str, *args: str) -> dict:
    with ipc.connect(sock) as s:
        s.sendall(json.dumps({"cmd": cmd, "args": list(args)}).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data)


def run_daemon(tmp_path: Path, prelude: str, transport: str) -> dict:
    """Start the daemon, ask it, stop it; the answers by command."""
    sock = tmp_path / "hauslicht.sock"
    env = {**os.environ,
           "HAUSLICHT_BACKENDS": "mock", "HAUSLICHT_STATE": str(tmp_path / "state.json"),
           "HAUSLICHT_SOCK": str(sock), "HAUSLICHT_NO_PANEL": "1", "HAUSLICHT_TRANSPORT": transport,
           "HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "config"),
           "XDG_STATE_HOME": str(tmp_path / "state"), "XDG_CACHE_HOME": str(tmp_path / "cache"),
           "XDG_RUNTIME_DIR": str(tmp_path / "run"),
           "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent",
           "DBUS_SYSTEM_BUS_ADDRESS": "unix:path=/nonexistent",
           "PYTHONPATH": str(REPO)}
    env.pop("HAUSLICHT_DEBUG", None)
    log = tmp_path / "daemon.log"
    with open(log, "wb") as out:
        proc = subprocess.Popen([sys.executable, "-c", prelude + TRIPWIRES + ENTRY], cwd=REPO, env=env,
                                stdout=out, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 30
        while True:
            if proc.poll() is not None:
                pytest.fail(f"daemon exited {proc.returncode}:\n{log.read_text()}")
            try:
                answers = {"status": ask(sock, "status")}
                break
            except (OSError, ValueError):  # not serving yet (no socket, no port file)
                if time.monotonic() > deadline:
                    pytest.fail(f"daemon did not serve:\n{log.read_text()}")
                time.sleep(0.2)
        answers.update({cmd: ask(sock, cmd) for cmd in ("get", "adapters")})
        answers["flick"] = ask(sock, "flick", "fast")
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    text = log.read_text()
    assert proc.returncode == 0, text
    assert "Traceback" not in text and "Linux-only" not in text, text
    return answers


def check_answers(answers: dict) -> None:
    assert answers["status"] == {"results": []}  # no bulbs yet: an answer, not an error
    got = answers["get"]["results"][0]
    assert got["screen"] == 100.0 and got["screen_on"] is True and got["lid_closed"] is False
    assert got["night"] is False and got["day"] is True and got["color_scheme"] is None
    assert got["setup"]["status"] == "missing"
    assert got["exclusive"]["active"] is False
    assert answers["adapters"]["results"][0]["adapters"] == []
    assert answers["adapters"]["results"][0]["chosen"] is None
    assert "hauslicht setup-root" in answers["flick"]["error"]  # fast needs the setup, which is Linux's


def test_daemon_serves_on_macos(tmp_path, monkeypatch):
    monkeypatch.delenv("HAUSLICHT_TRANSPORT", raising=False)  # macOS keeps the Unix socket
    check_answers(run_daemon(tmp_path, FAKE_DARWIN, "unix"))


def test_daemon_serves_on_windows_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("HAUSLICHT_TRANSPORT", "tcp")  # this side's ipc.connect: port file and token
    answers = run_daemon(tmp_path, GATE_OFF, "tcp")
    assert not (tmp_path / "hauslicht.sock").exists()
    check_answers(answers)
