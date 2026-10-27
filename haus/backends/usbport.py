"""USB port power: a USB port's 5 V switched on and off, for simple USB
lamps that only draw power (no data connection, so USB cannot see them).

The kernel (Linux 6.0+) has /sys/bus/usb/devices/<hub interface>/<port>/
disable: 1 clears the port's power (PORT_POWER), 0 powers it again, and it
reads back the state. A USB 3 socket is two logical ports (USB 2 and USB 3,
linked by `peer`) on one 5 V supply; both are switched. Writing needs root:
ports have no udev subsystem, so no udev rule can grant it; the root helper
install/usb-port-power (sudo, set up by setup-root.sh v6) does the write and
checks the port (connect_type "hotplug" only). The state is read here.

Whether the 5 V really drops is up to the hub: many laptops' own ports keep
it (the switch only stops data), hubs with per-port power switching drop it
(uhubctl's README lists them). Identify shows it: a lamp that does not
blink sits on a port that does not switch.

On/off only. A data device on the port (its `device` link) is disconnected
by switching off: discover names it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from . import Backend, NeedsAccess, Offline
from ..common import UserError
from ._bulbkit import Presence, tr

log = logging.getLogger("hauslicht")

SYSFS = Path("/sys")  # tests point it at a tmp tree
HELPER = "/usr/local/lib/hauslicht/usb-port-power"
SUDO: tuple[str, ...] = ("sudo", "-n")  # tests: the Python interpreter
HELPER_TIMEOUT_S = 10.0
PORT_RE = re.compile(r"(usb[0-9]{1,3}|[0-9]{1,3}-[0-9]{1,3}(?:\.[0-9]{1,3}){0,6})-port([0-9]{1,3})")
INTERFACE_RE = re.compile(r"[0-9]{1,3}-[0-9.]+:[0-9]{1,3}\.[0-9]{1,3}")


def _read(path: Path, default: str = "") -> str:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return default


def location(port: str) -> str:
    """The bus/port path a device on this port gets: usb1-port2 -> 1-2,
    1-1.4-port2 -> 1-1.4.2."""
    m = PORT_RE.fullmatch(port)
    if not m:
        return port
    hub, num = m.group(1), m.group(2)
    return f"{hub[3:]}-{num}" if hub.startswith("usb") else f"{hub}.{num}"


def _sort_key(port: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"[0-9]+", location(port)))


def describe_device(path: Path) -> str:
    """"Manufacturer Product" of a USB device directory, else vid:pid. A root
    hub's manufacturer is the kernel's version string: its product only."""
    product, maker = _read(path / "product"), _read(path / "manufacturer")
    if path.name.startswith("usb"):
        maker = ""
    if product:
        return product if not maker or product.lower().startswith(maker.lower()) else f"{maker} {product}"
    vid, pid = _read(path / "idVendor"), _read(path / "idProduct")
    return f"{vid}:{pid}" if vid else path.name


class Port:
    """One port's sysfs directory, read only."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.name = path.name

    @property
    def connect_type(self) -> str:
        return _read(self.path / "connect_type")

    @property
    def disabled(self) -> bool | None:
        value = _read(self.path / "disable")
        return None if value not in ("0", "1") else value == "1"

    def peer(self) -> Port | None:
        link = self.path / "peer"
        if not link.is_symlink():
            return None
        real = Path(os.path.realpath(link))
        return Port(real) if PORT_RE.fullmatch(real.name) and real.is_dir() else None

    def device(self) -> str | None:
        """The data device enumerated on this port (or its peer), by name."""
        for port in (self, self.peer()):
            if port is not None and (port.path / "device").exists():
                return describe_device(Path(os.path.realpath(port.path / "device")))
        return None

    def hub(self) -> str:
        """The hub the port belongs to: the interface directory's device."""
        return describe_device(Path(os.path.realpath(self.path)).parent.parent)


def ports() -> dict[str, Port]:
    """Every port of every hub, by name."""
    base = SYSFS / "bus" / "usb" / "devices"
    out: dict[str, Port] = {}
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return out
    for entry in entries:
        if not INTERFACE_RE.fullmatch(entry):
            continue
        try:
            names = os.listdir(base / entry)
        except OSError:
            continue
        for name in names:
            if PORT_RE.fullmatch(name) and (base / entry / name / "disable").exists():
                out[name] = Port(base / entry / name)
    return out


def find(name: str) -> Port | None:
    if not PORT_RE.fullmatch(name or ""):
        return None
    return ports().get(name)


class UsbPortBackend(Backend):
    kind = "usbport"
    title = "USB port power (on/off; only where the hub switches the port's 5 V)"
    product = "USB port"
    experimental = True
    autoconnect = False  # a port may feed a mouse or a disk: switched only by the user's choice
    fields = (
        {"key": "port", "label": "USB port", "label_de": "USB-Port",
         "type": "text", "required": True, "default": ""},
    )

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.presence = Presence(ctx, self._probe)

    # ------------------------------------------------------------ the API
    def caps(self, bulb) -> set[str]:
        return {"onoff"}

    def step(self, bulb) -> float:
        return 100.0

    def min_lit(self, bulb) -> float:
        return 100.0

    def details(self, bulb) -> dict:
        name = bulb.config.get("port", "")
        port = find(name)
        return {"model": self.product, "address": name, "location": location(name),
                "hub": port.hub() if port else None, "device": port.device() if port else None}

    def status(self, bulb) -> dict:
        online = self.presence.get(bulb.id)
        if online is None:
            online = find(bulb.config.get("port", "")) is not None
        return {"connected": bool(online), "online": online}

    async def _probe(self, bulb) -> dict | None:
        return {} if find(bulb.config.get("port", "")) is not None else None

    async def query(self, bulb) -> dict | None:
        port = find(bulb.config.get("port", ""))
        if port is None:
            return None
        states = [p.disabled for p in (port, port.peer()) if p is not None]
        if None in states:
            return None
        on = not all(states)
        return {"on": on, "percent": 100.0 if on else 0.0}

    async def switch(self, name: str, on: bool) -> None:
        """The root helper: `usb-port-power PORT on|off` via sudo."""
        if not os.path.exists(HELPER):
            raise NeedsAccess(tr("Switching USB ports needs the system setup v6 (Grant access…, or: "
                                 "hauslicht setup-root).",
                                 "USB-Ports schalten braucht die Systemeinrichtung v6 (Zugriff erlauben…, "
                                 "oder: hauslicht setup-root)."))
        proc = await asyncio.create_subprocess_exec(
            *SUDO, HELPER, name, "on" if on else "off", stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _out, err = await asyncio.wait_for(proc.communicate(), HELPER_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise OSError(f"usb-port-power {name}: no answer in {HELPER_TIMEOUT_S:.0f} s") from None
        why = err.decode(errors="replace").strip().splitlines()
        why = why[-1] if why else f"exit {proc.returncode}"
        if proc.returncode == 2:  # refused: not a hotplug port, or sudo said no
            raise UserError(f"{name}: {why}")
        if proc.returncode != 0:
            raise OSError(f"{name}: {why}")

    async def apply(self, bulb, percent: float) -> None:
        name = bulb.config.get("port", "")
        if find(name) is None:
            self.presence.lost(bulb, "no such USB port")
            raise Offline(f"{bulb.id}: USB port {name} is gone")
        await self.switch(name, percent > 0)
        self.presence.ok(bulb)

    async def discover(self, timeout: float) -> list[dict]:
        """Every hotplug port, a USB 3 socket's two ports as one entry (the
        lower-numbered), with its bus/port path, hub and attached device."""
        found, seen = [], set()
        all_ports = ports()
        for name in sorted(all_ports, key=_sort_key):
            port = all_ports[name]
            if name in seen or port.connect_type != "hotplug":
                continue
            peer = port.peer()
            seen.update({name} | ({peer.name} if peer else set()))
            where = location(name) + (f" / {location(peer.name)}" if peer else "")
            hub, device = port.hub(), port.device()
            label = tr(f"USB port {where} · {hub}", f"USB-Port {where} · {hub}")
            if device:
                label += tr(f" · {device}: switching off disconnects it",
                            f" · {device}: Ausschalten trennt es")
            entry = {"kind": self.kind, "address": name, "name": label, "location": where, "hub": hub,
                     "device": device, "config": {"port": name}}
            found.append(entry)
        return found

    async def add(self, config: dict) -> dict:
        name = str(config.get("port", "")).strip()
        port = find(name)
        if port is None:
            raise UserError(tr(f"no USB port {name} (hauslicht discover usbport lists them)",
                               f"Kein USB-Port {name} (hauslicht discover usbport listet sie)"))
        if port.connect_type != "hotplug":
            raise UserError(tr(f"{name} is not a socket (connect_type {port.connect_type or '?'}): never switched",
                               f"{name} ist keine Buchse (connect_type {port.connect_type or '?'}): "
                               "wird nie geschaltet"))
        if not os.path.exists(HELPER):
            raise NeedsAccess(tr("Switching USB ports needs the system setup v6 (Grant access…, or: "
                                 "hauslicht setup-root).",
                                 "USB-Ports schalten braucht die Systemeinrichtung v6 (Zugriff erlauben…, "
                                 "oder: hauslicht setup-root)."))
        peer = port.peer()
        if peer is not None and _sort_key(peer.name) < _sort_key(name):
            port, peer = peer, port  # one socket, one bulb: named by its lower port, as discover lists it
        config = {"port": port.name}
        if peer is not None:
            config["peer"] = peer.name
        config["id"] = port.name
        return config

    async def remove(self, bulb) -> None:
        self.presence.forget(bulb.id)

    async def stop(self) -> None:  # the port stays as it is
        await self.presence.stop()
