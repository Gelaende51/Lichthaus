"""USB port power (the usbport kind): the root helper install/usb-port-power
and the backend, both against a fake /sys tree. Nothing here touches the real
/sys, sudo or a USB port."""

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "install" / "usb-port-power"
PCI = "devices/pci0000:00/0000:00:14.0"

# the fake machine: root hubs usb1 (USB 2) and usb2 (USB 3) share two sockets
# (peers), usb1-port3 is the built-in camera; an external hub 1-1 sits in the
# first socket with a receiver in its port 1
PORTS = {
    # port: (hub interface dir under PCI, connect_type, attached device or None, peer or None)
    "usb1-port1": ("usb1/1-0:1.0", "hotplug", "1-1", "usb2-port1"),
    "usb1-port2": ("usb1/1-0:1.0", "hotplug", None, "usb2-port2"),
    "usb1-port3": ("usb1/1-0:1.0", "hardwired", "1-3", None),
    "usb2-port1": ("usb2/2-0:1.0", "hotplug", None, "usb1-port1"),
    "usb2-port2": ("usb2/2-0:1.0", "hotplug", None, "usb1-port2"),
    "1-1-port1": ("usb1/1-1/1-1:1.0", "hotplug", "1-1.1", None),
    "1-1-port2": ("usb1/1-1/1-1:1.0", "hotplug", None, None),
    "1-1-port4": ("usb1/1-1/1-1:1.0", "not used", None, None),
}
DEVICES = {  # device dir under PCI: (product, manufacturer)
    "usb1": ("xHCI Host Controller", "Linux"),
    "usb2": ("xHCI Host Controller", "Linux"),
    "usb1/1-1": ("USB2.0 Hub", "GenesysLogic"),
    "usb1/1-1/1-1.1": ("USB Receiver", "Logitech"),
    "usb1/1-3": ("Integrated Camera", "Chicony"),
}


def port_dir(root: Path, port: str) -> Path:
    return root / PCI / PORTS[port][0] / port


def make_sysfs(root: Path) -> Path:
    """A /sys with bus/usb/devices links into devices/, like the kernel's."""
    bus = root / "bus" / "usb" / "devices"
    bus.mkdir(parents=True)
    for rel, (product, maker) in DEVICES.items():
        d = root / PCI / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "product").write_text(product + "\n")
        (d / "manufacturer").write_text(maker + "\n")
        os.symlink(os.path.relpath(d, bus), bus / d.name)
    for port, (iface, ctype, dev, _peer) in PORTS.items():
        d = port_dir(root, port)
        d.mkdir(parents=True, exist_ok=True)
        (d / "connect_type").write_text(ctype + "\n")
        (d / "disable").write_text("0\n")
        if dev:
            target = next(root / PCI / rel for rel in DEVICES if rel.endswith("/" + dev) or rel == dev)
            os.symlink(os.path.relpath(target, d), d / "device")
        link = bus / Path(iface).name
        if not link.exists():
            os.symlink(os.path.relpath(d.parent, bus), link)
    for port, (_i, _c, _d, peer) in PORTS.items():
        if peer:
            d = port_dir(root, port)
            os.symlink(os.path.relpath(port_dir(root, peer), d), d / "peer")
    return root


@pytest.fixture
def sysfs(tmp_path):
    return make_sysfs(tmp_path / "sys")


def disabled(root: Path, port: str) -> str:
    return (port_dir(root, port) / "disable").read_text().strip()


def helper(root: Path, *args: str, env_root: bool = True) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "HAUSLICHT_TEST_USB_SYSFS"}
    if env_root:
        env["HAUSLICHT_TEST_USB_SYSFS"] = str(root)
    return subprocess.run([sys.executable, "-I", str(HELPER), *args], env=env,
                          capture_output=True, text=True, timeout=20)


# ------------------------------------------------------------------ the helper

@pytest.mark.skipif(os.geteuid() == 0, reason="the test sysfs is ignored as root")
def test_helper_switches_a_hotplug_port_and_its_peer(sysfs):
    assert helper(sysfs, "usb1-port2", "get").stdout == "on\n"
    p = helper(sysfs, "usb1-port2", "off")
    assert p.returncode == 0, p.stderr
    assert disabled(sysfs, "usb1-port2") == "1" and disabled(sysfs, "usb2-port2") == "1"
    assert disabled(sysfs, "usb1-port1") == "0" and disabled(sysfs, "usb2-port1") == "0"
    assert helper(sysfs, "usb1-port2", "get").stdout == "off\n"
    assert helper(sysfs, "usb2-port2", "get").stdout == "off\n"  # from the peer's side too
    assert helper(sysfs, "usb2-port2", "on").returncode == 0
    assert disabled(sysfs, "usb1-port2") == "0" and disabled(sysfs, "usb2-port2") == "0"
    # one of the two still powered: the 5 V is on
    (port_dir(sysfs, "usb2-port2") / "disable").write_text("1\n")
    assert helper(sysfs, "usb1-port2", "get").stdout == "on\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="the test sysfs is ignored as root")
def test_helper_switches_an_external_hub_port_without_peer(sysfs):
    assert helper(sysfs, "1-1-port2", "off").returncode == 0
    assert disabled(sysfs, "1-1-port2") == "1"
    assert helper(sysfs, "1-1-port2", "get").stdout == "off\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="the test sysfs is ignored as root")
@pytest.mark.parametrize("port", ["usb1-port3", "1-1-port4"])
def test_helper_refuses_hardwired_and_unused_ports(sysfs, port):
    for action in ("off", "on", "get"):
        p = helper(sysfs, port, action)
        assert p.returncode == 2 and "not hotplug" in p.stderr
    assert disabled(sysfs, port) == "0"


@pytest.mark.parametrize("port", [
    "../../etc/passwd", "usb1-port2/../usb1-port3", "usb1-port2/", "1-..-port2", "1-1.-port2",
    "1-.1-port2", "usb1-port2\n", " usb1-port2", "usb1-port", "port2", "usb1-port2;id", "*-port2",
    "usb?-port2", "hci0", ""])
def test_helper_refuses_bad_names(sysfs, port):
    p = helper(sysfs, port, "off")
    assert p.returncode == 2
    assert all(disabled(sysfs, q) == "0" for q in PORTS)


def test_helper_refuses_bad_usage(sysfs):
    for args in ((), ("usb1-port2",), ("usb1-port2", "toggle"), ("usb1-port2", "off", "x")):
        assert helper(sysfs, *args).returncode == 2
    assert all(disabled(sysfs, q) == "0" for q in PORTS)


@pytest.mark.skipif(os.geteuid() == 0, reason="the test sysfs is ignored as root")
def test_helper_refuses_unknown_ports_and_planted_links(sysfs, tmp_path):
    assert helper(sysfs, "usb9-port1", "off").returncode == 2
    # a peer leading out of devices/ (a planted port elsewhere) is refused, nothing written
    outside = tmp_path / "elsewhere" / "1-0:1.0" / "usb7-port1"
    outside.mkdir(parents=True)
    (outside / "connect_type").write_text("hotplug\n")
    (outside / "disable").write_text("0\n")
    peer = port_dir(sysfs, "1-1-port2") / "peer"
    os.symlink(outside, peer)
    p = helper(sysfs, "1-1-port2", "off")
    assert p.returncode == 2 and "not under" in p.stderr
    assert disabled(sysfs, "1-1-port2") == "0" and (outside / "disable").read_text() == "0\n"
    peer.unlink()
    # a disable attribute that is a link is not written through
    target = tmp_path / "victim"
    target.write_text("keep\n")
    d = port_dir(sysfs, "1-1-port2") / "disable"
    d.unlink()
    os.symlink(target, d)
    p = helper(sysfs, "1-1-port2", "off")
    assert p.returncode == 2 and target.read_text() == "keep\n"


def test_helper_ignores_the_test_root_as_root(monkeypatch, tmp_path):
    loader = importlib.machinery.SourceFileLoader("usb_port_power", str(HELPER))
    spec = importlib.util.spec_from_loader("usb_port_power", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    monkeypatch.setenv(mod.TEST_ENV, str(tmp_path))
    monkeypatch.setattr(mod.os, "geteuid", lambda: 0)
    assert mod.sysfs_root() == "/sys"  # sudo: euid 0
    monkeypatch.setattr(mod.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(mod.os, "getuid", lambda: 0)
    assert mod.sysfs_root() == "/sys"
    monkeypatch.setattr(mod.os, "getuid", lambda: 1000)
    assert mod.sysfs_root() == str(tmp_path.resolve())
    monkeypatch.delenv(mod.TEST_ENV)
    assert mod.sysfs_root() == "/sys"


def test_setup_v6_installs_the_helper_for_sudo():
    script = (ROOT / "install" / "setup-root.sh").read_text()
    assert (ROOT / "install" / "setup-version").read_text().strip() == "6"
    assert '"$here/usb-port-power" /usr/local/lib/hauslicht/usb-port-power' in script
    assert ("NOPASSWD: /usr/local/lib/hauslicht/conn-update, /usr/local/lib/hauslicht/hci-claim, "
            "/usr/local/lib/hauslicht/usb-port-power\"") in script
    assert "SETENV" not in script  # the test root can never be passed through sudo
    assert os.access(HELPER, os.X_OK)
    assert HELPER.read_text().startswith("#!/usr/bin/python3 -I\n")


# ------------------------------------------------------------------ the backend

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from haus.backends import NeedsAccess, Offline, usbport  # noqa: E402
from haus.common import UserError  # noqa: E402
from haus.profiles import new_state  # noqa: E402
from test_core import cmd as daemon_cmd, run as run_daemon, settle  # noqa: E402


class FakeCtx:
    def __init__(self):
        self.events = []

    def bulbs(self):
        return []

    def power_returned(self, bid, gap):
        self.events.append(("returned", bid, gap))

    def changed(self):
        self.events.append(("changed",))


def fake_helper(tmp_path: Path, exit_code: int = 0, message: str = "") -> tuple[Path, Path]:
    """A stand-in for the root helper: logs its arguments, exits as told."""
    log = tmp_path / "helper.log"
    script = tmp_path / "fake-usb-port-power"
    script.write_text(
        "import sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"sys.stderr.write({message!r})\n"
        f"sys.exit({exit_code})\n")
    return script, log


@pytest.fixture
def ports(sysfs, tmp_path, monkeypatch):
    monkeypatch.setattr(usbport, "SYSFS", sysfs)
    monkeypatch.setattr(usbport, "SUDO", (sys.executable, "-I"))
    monkeypatch.setattr(usbport, "tr", lambda en, de: en)
    script, log = fake_helper(tmp_path)
    monkeypatch.setattr(usbport, "HELPER", str(script))
    return SimpleNamespace(root=sysfs, log=log, tmp=tmp_path)


def bulb(bid, config):
    return SimpleNamespace(id=bid, name=bid, config=config)


def test_scan_lists_hotplug_sockets_with_location_hub_and_device(ports):
    found = asyncio.run(usbport.UsbPortBackend(FakeCtx()).discover(0))
    assert [f["address"] for f in found] == ["usb1-port1", "1-1-port1", "1-1-port2", "usb1-port2"]
    by = {f["address"]: f for f in found}
    assert by["usb1-port1"] == {
        "kind": "usbport", "address": "usb1-port1", "location": "1-1 / 2-1", "hub": "xHCI Host Controller",
        "device": "GenesysLogic USB2.0 Hub", "name": "USB port 1-1", "warning": True,
        "note": "1-1 / 2-1 · xHCI Host Controller · GenesysLogic USB2.0 Hub attached: switching off disconnects it",
        "config": {"port": "usb1-port1"}}
    assert by["1-1-port1"]["location"] == "1-1.1" and by["1-1-port1"]["hub"] == "GenesysLogic USB2.0 Hub"
    assert by["1-1-port1"]["device"] == "Logitech USB Receiver"
    assert by["usb1-port2"]["device"] is None
    assert by["usb1-port2"]["name"] == "USB port 1-2" and by["usb1-port2"]["note"] == "1-2 / 2-2 · xHCI Host Controller"
    assert "warning" not in by["usb1-port2"]
    assert by["1-1-port2"]["name"] == "USB port 1-1.2" and "warning" not in by["1-1-port2"]
    assert "disconnects" not in by["1-1-port2"]["note"]
    # hardwired, not used and the USB 3 halves of a socket are never listed
    assert not {"usb1-port3", "1-1-port4", "usb2-port1", "usb2-port2"} & set(by)
    assert usbport.UsbPortBackend.autoconnect is False and usbport.UsbPortBackend.bluetooth is False


def test_a_device_on_the_usb3_peer_counts_as_attached(ports):
    d = port_dir(ports.root, "usb2-port2")
    os.symlink(os.path.relpath(ports.root / PCI / "usb1/1-3", d), d / "device")
    found = asyncio.run(usbport.UsbPortBackend(FakeCtx()).discover(0))
    entry = next(f for f in found if f["address"] == "usb1-port2")
    assert entry["device"] == "Chicony Integrated Camera" and entry["name"] == "USB port 1-2"
    assert entry["warning"] is True and "Chicony Integrated Camera attached: switching off disconnects it" in entry["note"]


def test_add_names_the_socket_by_its_lower_port_and_refuses_others(ports):
    be = usbport.UsbPortBackend(FakeCtx())
    cfg = asyncio.run(be.add({"port": " usb2-port2 "}))
    assert cfg == {"port": "usb1-port2", "peer": "usb2-port2", "id": "usb1-port2"}
    assert asyncio.run(be.add({"port": "1-1-port2"})) == {"port": "1-1-port2", "id": "1-1-port2"}
    with pytest.raises(UserError, match="not a socket"):
        asyncio.run(be.add({"port": "usb1-port3"}))
    for bad in ("usb9-port1", "../usb1-port2", "usb1-port2/.."):
        with pytest.raises(UserError, match="no USB port"):
            asyncio.run(be.add({"port": bad}))


def test_onoff_through_the_helper_state_from_disable(ports):
    async def body():
        ctx = FakeCtx()
        be = usbport.UsbPortBackend(ctx)
        b = bulb("usbport:usb1-port2", {"port": "usb1-port2", "peer": "usb2-port2"})
        assert be.caps(b) == {"onoff"} and be.step(b) == 100.0 and be.min_lit(b) == 100.0
        await be.apply(b, 0)
        await be.apply(b, 37)
        assert ports.log.read_text().splitlines() == ["usb1-port2 off", "usb1-port2 on"]
        assert be.status(b) == {"connected": True, "online": True}
        assert await be.query(b) == {"on": True, "percent": 100.0}
        (port_dir(ports.root, "usb1-port2") / "disable").write_text("1\n")
        assert await be.query(b) == {"on": True, "percent": 100.0}  # the peer still feeds the socket
        (port_dir(ports.root, "usb2-port2") / "disable").write_text("1\n")
        assert await be.query(b) == {"on": False, "percent": 0.0}
        assert be.details(b)["location"] == "1-2" and be.details(b)["hub"] == "xHCI Host Controller"
        await be.stop()
    asyncio.run(body())


def test_helper_errors_reach_the_user(ports, monkeypatch):
    async def body():
        be = usbport.UsbPortBackend(FakeCtx())
        b = bulb("usbport:1-1-port2", {"port": "1-1-port2"})
        script, _log = fake_helper(ports.tmp, 2, "refused: 1-1-port2: connect_type is 'hardwired'\n")
        monkeypatch.setattr(usbport, "HELPER", str(script))
        with pytest.raises(UserError, match="hardwired"):
            await be.apply(b, 100)
        script, _log = fake_helper(ports.tmp, 1, "1-1-port2: off failed: Invalid argument\n")
        monkeypatch.setattr(usbport, "HELPER", str(script))
        with pytest.raises(OSError, match="Invalid argument"):
            await be.apply(b, 0)
        monkeypatch.setattr(usbport, "HELPER", str(ports.tmp / "not-installed"))
        with pytest.raises(NeedsAccess, match="system setup v6"):
            await be.apply(b, 0)
        with pytest.raises(NeedsAccess, match="system setup v6"):
            await be.add({"port": "1-1-port2"})
        await be.stop()
    asyncio.run(body())


def test_a_gone_port_is_offline(ports):
    async def body():
        ctx = FakeCtx()
        be = usbport.UsbPortBackend(ctx)
        b = bulb("usbport:1-1-port2", {"port": "1-1-port2"})
        await be.apply(b, 100)
        os.unlink(ports.root / "bus" / "usb" / "devices" / "1-1:1.0")  # the hub unplugged
        with pytest.raises(Offline):
            await be.apply(b, 0)
        assert be.status(b) == {"connected": False, "online": False}
        assert ports.log.read_text().splitlines() == ["1-1-port2 on"]
        await be.stop()
    asyncio.run(body())


@pytest.mark.skipif(os.geteuid() == 0, reason="the test sysfs is ignored as root")
def test_backend_with_the_real_helper_on_the_fake_tree(ports, monkeypatch):
    monkeypatch.setattr(usbport, "HELPER", str(HELPER))
    monkeypatch.setenv("HAUSLICHT_TEST_USB_SYSFS", str(ports.root))

    async def body():
        be = usbport.UsbPortBackend(FakeCtx())
        b = bulb("usbport:usb1-port2", {"port": "usb1-port2"})
        await be.apply(b, 0)
        assert disabled(ports.root, "usb1-port2") == "1" and disabled(ports.root, "usb2-port2") == "1"
        assert await be.query(b) == {"on": False, "percent": 0.0}
        await be.apply(b, 100)
        assert await be.query(b) == {"on": True, "percent": 100.0}
        await be.stop()
    asyncio.run(body())


def test_daemon_discover_add_and_known(ports, tmp_path, monkeypatch):
    from haus import backends as registry
    reg = dict(registry.registry())
    reg["usbport"] = registry.Info("usbport", usbport.UsbPortBackend, [])
    monkeypatch.setattr(registry, "_registry", reg)

    async def body(d):
        out = await daemon_cmd(d, "discover", "usbport")
        assert [x["address"] for x in out["discovered"]] == ["usb1-port1", "1-1-port1", "1-1-port2", "usb1-port2"]
        assert not any(x["known"] for x in out["discovered"])
        await daemon_cmd(d, "add", "usbport", "port=usb2-port2", "name=Shelf")
        assert d.bulbs["usbport:usb1-port2"].config == {"port": "usb1-port2", "peer": "usb2-port2"}
        out = await daemon_cmd(d, "discover", "usbport")
        assert [x["address"] for x in out["discovered"] if x["known"]] == ["usb1-port2"]
        await settle(d)
        assert set(ports.log.read_text().split()) <= {"usb1-port2", "on", "off"}
    run_daemon(tmp_path, new_state(), body)
