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
