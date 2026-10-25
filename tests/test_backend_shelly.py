"""Shelly backend against local aiohttp mock devices (Gen1 and Gen2 RPC)."""

import asyncio
import base64
import time

import pytest

pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402

from haus.backends import Offline  # noqa: E402
from haus.backends import shelly as shelly_mod  # noqa: E402
from haus.backends.shelly import ShellyBackend  # noqa: E402
from haus.common import UserError  # noqa: E402
from lan_fakes import FakeCtx, bulb, serve  # noqa: E402


class FakeGen1:
    """Shelly Duo-like Gen1 device."""

    def __init__(self, password=None, lights=1, kind="vintage"):
        self.password = password
        self.light = {"ison": False, "brightness": 50}
        if kind == "duo":      # Shelly Duo: white temperature
            self.light["temp"] = 2700
        elif kind == "color":  # Shelly Bulb / Duo RGBW: colour and white mode
            self.light.update(mode="white", temp=4000, red=255, green=255, blue=255, white=0, gain=100)
        self.lights = lights
        self.default_state = "last"
        self.uptime = 1000
        self.requests: list[tuple] = []

    def _auth(self, request):
        if not self.password:
            return True
        want = "Basic " + base64.b64encode(f"admin:{self.password}".encode()).decode()
        return request.headers.get("Authorization") == want

    def app(self):
        async def shelly(request):
            return web.json_response({"type": "SHBDUO-1", "mac": "AABBCCDDEEFF", "auth": bool(self.password)})

        async def status(request):
            if not self._auth(request):
                return web.Response(status=401)
            return web.json_response({"uptime": self.uptime, "lights": [self.light] * self.lights})

        async def light(request):
            if not self._auth(request):
                return web.Response(status=401)
            q = request.query
            self.requests.append(("light", dict(q), time.monotonic()))
            if "turn" in q:
                self.light["ison"] = q["turn"] == "on"
            for key in ("brightness", "red", "green", "blue", "white", "gain", "temp"):
                if key in q:
                    self.light[key] = int(q[key])
            if "mode" in q:
                self.light["mode"] = q["mode"]
            return web.json_response(self.light)

        async def settings(request):
            if not self._auth(request):
                return web.Response(status=401)
            self.default_state = request.query.get("default_state", self.default_state)
            return web.json_response({"default_state": self.default_state})

        app = web.Application()
        app.router.add_get("/shelly", shelly)
        app.router.add_get("/status", status)
        app.router.add_get("/light/0", light)
        app.router.add_get("/settings/light/0", settings)
        return app


class FakeGen2:
    """Shelly Plus Wall Dimmer-like Gen2 device (RPC over HTTP)."""

    NS = {"light": "Light", "cct": "CCT", "rgbcct": "RGBCCT", "rgbw": "RGBW", "rgb": "RGB"}

    def __init__(self, has_light=True, component="light", ct_range=None):
        self.has_light = has_light
        self.component = component
        self.ns = self.NS[component]
        self.ct_range = ct_range
        self.light = {"id": 0, "output": False, "brightness": 40}
        if component in ("rgb", "rgbw", "rgbcct"):
            self.light["rgb"] = [255, 255, 255]
        if component == "rgbw":
            self.light["white"] = 255
        if component in ("cct", "rgbcct"):
            self.light["ct"] = 4000
        if component == "rgbcct":
            self.light["mode"] = "cct"
        self.initial_state = "restore_last"
        self.uptime = 500
        self.calls: list[tuple] = []

    def app(self):
        async def shelly(request):
            return web.json_response({"id": "shellywalldimmer-aabbcc", "mac": "112233445566",
                                      "model": "SNDM-0013US", "gen": 2, "auth_en": False})

        async def rpc(request):
            body = await request.json()
            method, params = body["method"], body.get("params", {})
            self.calls.append((method, params))
            if method == "Shelly.GetStatus":
                result = {"sys": {"uptime": self.uptime}}
                if self.has_light:
                    result[f"{self.component}:0"] = self.light
                return web.json_response({"id": body["id"], "result": result})
            if method == f"{self.ns}.Set":
                for key in ("on", "brightness", "rgb", "white", "ct", "mode"):
                    if key in params:
                        self.light["output" if key == "on" else key] = params[key]
                return web.json_response({"id": body["id"], "result": None})
            if method == f"{self.ns}.GetConfig" and self.ct_range:
                return web.json_response({"id": body["id"], "result": {"id": 0, "ct_range": self.ct_range}})
            if method == f"{self.ns}.SetConfig":
                self.initial_state = params["config"]["initial_state"]
                return web.json_response({"id": body["id"], "result": {"restart_required": False}})
            return web.json_response({"id": body["id"], "error": {"code": 404, "message": "No handler"}})

        app = web.Application()
        app.router.add_get("/shelly", shelly)
        app.router.add_post("/rpc", rpc)
        return app


def run(coro):
    return asyncio.run(coro)


def test_gen1_add_apply_powerup_and_auth():
    async def main():
        dev = FakeGen1(password="pw")
        runner, host = await serve(dev.app())
        be = ShellyBackend(FakeCtx())
        try:
            with pytest.raises(UserError, match="password"):
                await be.add({"host": host})
            config = await be.add({"host": host, "password": "pw", "username": "admin", "channel": 0})
            assert config["gen"] == 1 and config["id"] == "aabbccddeeff"
            b = bulb("shelly", config["id"], **config)
            await be.apply(b, 37.4)
            assert dev.light == {"ison": True, "brightness": 37}
            await be.apply(b, 0.4)  # never below 1 while on
            assert dev.light["brightness"] == 1
            await be.apply(b, 0)
            assert dev.light["ison"] is False
            assert await be.set_powerup(b, "off") and dev.default_state == "off"
            assert await be.set_powerup(b, "restore") and dev.default_state == "last"
            assert be.status(b)["online"] is True
            assert await be.query(b) == {"on": False, "percent": 0.0}
        finally:
            await be.stop()
            await runner.cleanup()
    run(main())


def test_gen2_add_apply_powerup():
    async def main():
        dev = FakeGen2()
        runner, host = await serve(dev.app())
        be = ShellyBackend(FakeCtx())
        try:
            config = await be.add({"host": host, "channel": 0})
            assert config["gen"] == 2 and config["id"] == "112233445566"
            b = bulb("shelly", config["id"], **config)
            await be.apply(b, 62.6)
            assert dev.light["output"] is True and dev.light["brightness"] == 63
            await be.apply(b, 0)
            assert dev.light["output"] is False
            assert ("Light.Set", {"id": 0, "on": False}) in dev.calls
            assert await be.set_powerup(b, "on") and dev.initial_state == "on"
            assert await be.set_powerup(b, "restore") and dev.initial_state == "restore_last"
        finally:
            await be.stop()
            await runner.cleanup()
    run(main())


def test_gen2_without_light_is_refused():
    async def main():
        runner, host = await serve(FakeGen2(has_light=False).app())
        try:
            with pytest.raises(UserError, match="no light channel"):
                await ShellyBackend(FakeCtx()).add({"host": host})
        finally:
            await runner.cleanup()
    run(main())


def test_unreachable_is_offline_and_comes_back():
    async def main():
        dev = FakeGen2()
        runner, host = await serve(dev.app())
        ctx = FakeCtx()
        be = ShellyBackend(ctx)
        config = await be.add({"host": host})
        b = bulb("shelly", config["id"], **config)
        try:
            await be._probe(b)
            assert ("returned", b.id, None) in ctx.events  # first contact
            await runner.cleanup()
            with pytest.raises(Offline):
                await be.apply(b, 50)
            assert be.status(b) == {"connected": False, "online": False}
            # back after a power cut: the uptime restarted
            dev.uptime = 2
            runner = await _restart(dev, host)
            ctx.events.clear()
            await be._probe(b)
            returned = [e for e in ctx.events if e[0] == "returned"]
            assert len(returned) == 1 and returned[0][2] is not None
            assert be.status(b)["online"] is True
        finally:
            await be.stop()
            await runner.cleanup()
    run(main())


def test_reboot_between_polls_reports_a_loss_first():
    async def main():
        dev = FakeGen1()
        runner, host = await serve(dev.app())
        ctx = FakeCtx()
        be = ShellyBackend(ctx)
        b = bulb("shelly", "x", host=host, gen=1, channel=0)
        try:
            await be._probe(b)
            dev.uptime = 1
            ctx.events.clear()
            seen = []
            ctx.changed = lambda: seen.append(be.status(b)["online"])
            await be._probe(b)
            assert seen[0] is False and seen[-1] is True  # away, then back
            assert [e for e in ctx.events if e[0] == "returned"][0][2] < 15
        finally:
            await be.stop()
            await runner.cleanup()
    run(main())


def test_apply_is_rate_limited():
    async def main():
        dev = FakeGen1()
        runner, host = await serve(dev.app())
        be = ShellyBackend(FakeCtx())
        b = bulb("shelly", "x", host=host, gen=1, channel=0)
        try:
            for p in (10, 20, 30):
                await be.apply(b, p)
            times = [t for (_, _, t) in dev.requests]
            assert all(b2 - a >= 0.28 for a, b2 in zip(times, times[1:]))
            assert dev.light["brightness"] == 30
        finally:
            await be.stop()
            await runner.cleanup()
    run(main())


def test_discover_uses_mdns_results(monkeypatch):
    async def main():
        runner1, host1 = await serve(FakeGen1().app())
        runner2, host2 = await serve(FakeGen2().app())

        async def fake_browse(types, timeout):
            out = []
            for name, host, t in (("shellyduo-1", host1, "_http._tcp.local."),
                                  ("shellywalldimmer-2", host2, "_shelly._tcp.local."),
                                  ("printer", host2, "_http._tcp.local.")):
                ip, port = host.split(":")
                out.append({"type": t, "name": name, "addresses": [ip], "port": int(port),
                            "server": "", "properties": {}})
            return out

        monkeypatch.setattr(shelly_mod, "mdns_browse", fake_browse)
        try:
            found = await ShellyBackend(FakeCtx()).discover(0.1)
        finally:
            await runner1.cleanup()
            await runner2.cleanup()
        assert sorted(d["config"]["host"] for d in found) == sorted([host1, host2])
        assert all(d["kind"] == "shelly" for d in found)
    run(main())


async def _restart(dev, host):
    from aiohttp import web as _web
    runner = _web.AppRunner(dev.app())
    await runner.setup()
    ip, port = host.split(":")
    await _web.TCPSite(runner, ip, int(port)).start()
    return runner


def test_steady_uptime_is_no_reboot():
    async def main():
        dev = FakeGen1()
        runner, host = await serve(dev.app())
        ctx = FakeCtx()
        be = ShellyBackend(ctx)
        b = bulb("shelly", "x", host=host, gen=1, channel=0)
        try:
            for up in (1000, 1000, 1006, 1012):
                dev.uptime = up
                await be._probe(b)
            assert [e for e in ctx.events if e[0] == "returned"] == [("returned", b.id, None)]
        finally:
            await be.stop()
            await runner.cleanup()
    run(main())


def test_gen1_colour_caps_per_light():
    async def main():
        be = ShellyBackend(FakeCtx())
        for kind, caps, kelvin in (("vintage", set(), (2700, 6500)), ("duo", {"ct"}, (2700, 6500)),
                                   ("color", {"ct", "hs"}, (3000, 6500))):
            runner, host = await serve(FakeGen1(kind=kind).app())
            config = await be.add({"host": host})
            b = bulb("shelly", kind, **config)
            assert be.caps(b) == {"onoff", "dim", "powerup", "flick"} | caps, kind
            assert be.ct_range(b) == kelvin
            await runner.cleanup()
        await be.stop()
    run(main())


def test_gen1_colour_bulb_modes_keep_level_and_dark():
    async def main():
        dev = FakeGen1(kind="color")
        runner, host = await serve(dev.app())
        be = ShellyBackend(FakeCtx())
        b = bulb("shelly", "c", host=host, gen=1)  # an older config: the next poll learns the colour
        try:
            await be._probe(b)
            assert b.config["color"] == ["ct", "hs"]
            assert await be.apply_color(b, {"mode": "hs", "hue": 0, "sat": 100}) is False  # dark: nothing
            assert dev.requests == []
            await be.apply(b, 30)
            assert dev.requests[-1][1] == {"turn": "on", "brightness": "30"}  # white mode: brightness
            assert await be.apply_color(b, {"mode": "hs", "hue": 120, "sat": 50})
            assert dev.requests[-1][1] == {"mode": "color", "red": "128", "green": "255", "blue": "128",
                                           "white": "0", "gain": "30"}
            await be.apply(b, 45)  # colour mode: the level is gain
            assert dev.requests[-1][1] == {"turn": "on", "gain": "45"}
            assert dev.light["mode"] == "color" and dev.light["gain"] == 45
            assert await be.apply_color(b, {"mode": "ct", "kelvin": 2000})  # clamped to 3000
            assert dev.requests[-1][1] == {"mode": "white", "temp": "3000", "brightness": "45"}
            assert dev.light["brightness"] == 45 and dev.light["ison"]
            await be.apply(b, 0)
            n = len(dev.requests)
            assert await be.apply_color(b, {"mode": "ct", "kelvin": 4000}) is False
            assert len(dev.requests) == n and not dev.light["ison"]
        finally:
            await be.stop()
            await runner.cleanup()
    run(main())


def test_duo_temp_alone_and_gen2_without_colour():
    async def main():
        duo = FakeGen1(kind="duo")
        runner, host = await serve(duo.app())
        be = ShellyBackend(FakeCtx())
        try:
            config = await be.add({"host": host})
            b = bulb("shelly", "d", **config)
            await be.apply(b, 60)
            assert await be.apply_color(b, {"mode": "hs", "hue": 10, "sat": 10}) is False  # white only
            assert await be.apply_color(b, {"mode": "ct", "kelvin": 5000})
            assert duo.requests[-1][1] == {"temp": "5000"} and duo.light["brightness"] == 60
            gen2 = bulb("shelly", "g", host=host, gen=2, color=["ct", "hs"])  # a `light`: never colour
            assert be.caps(gen2) == {"onoff", "dim", "powerup", "flick"}
        finally:
            await be.stop()
            await runner.cleanup()
    run(main())


def test_gen2_colour_caps_per_component():
    async def main():
        be = ShellyBackend(FakeCtx())
        for comp, ct_range, caps, kelvin in (("light", None, set(), (2700, 6500)),
                                             ("cct", [3000, 5700], {"ct"}, (3000, 5700)),
                                             ("rgbcct", None, {"ct", "hs"}, (2700, 6500)),
                                             ("rgbw", None, {"hs"}, (2700, 6500)),
                                             ("rgb", None, {"hs"}, (2700, 6500))):
            runner, host = await serve(FakeGen2(component=comp, ct_range=ct_range).app())
            config = await be.add({"host": host})
            assert config["component"] == comp
            b = bulb("shelly", comp, **config)
            assert be.caps(b) == {"onoff", "dim", "powerup", "flick"} | caps, comp
            assert be.ct_range(b) == kelvin
            await runner.cleanup()
        await be.stop()
    run(main())


def test_gen2_colour_is_sent_only_while_lit_and_keeps_the_level():
    async def main():
        be = ShellyBackend(FakeCtx())
        try:
            dev = FakeGen2(component="rgbcct")
            runner, host = await serve(dev.app())
            config = await be.add({"host": host})
            b = bulb("shelly", "rc", **config)
            assert await be.apply_color(b, {"mode": "hs", "hue": 0, "sat": 100}) is False  # dark
            assert not any(m == "RGBCCT.Set" for m, _ in dev.calls)
            await be.apply(b, 30)
            assert dev.calls[-1] == ("RGBCCT.Set", {"id": 0, "on": True, "brightness": 30})
            assert await be.apply_color(b, {"mode": "hs", "hue": 120, "sat": 50})
            assert dev.calls[-1] == ("RGBCCT.Set", {"id": 0, "rgb": [128, 255, 128], "mode": "rgb"})
            assert await be.apply_color(b, {"mode": "ct", "kelvin": 9000})  # clamped to 6500
            assert dev.calls[-1] == ("RGBCCT.Set", {"id": 0, "ct": 6500, "mode": "cct"})
            assert dev.light["brightness"] == 30 and dev.light["output"] is True
            assert await be.set_powerup(b, "off") and dev.initial_state == "off"
            await runner.cleanup()

            dev = FakeGen2(component="rgbw")
            runner, host = await serve(dev.app())
            b = bulb("shelly", "rw", **await be.add({"host": host}))
            await be.apply(b, 50)
            assert await be.apply_color(b, {"mode": "ct", "kelvin": 3000}) is False  # no white temperature
            assert await be.apply_color(b, {"mode": "hs", "hue": 240, "sat": 100})
            assert dev.calls[-1] == ("RGBW.Set", {"id": 0, "rgb": [0, 0, 255], "white": 0})
            await runner.cleanup()

            dev = FakeGen2(component="cct", ct_range=[2700, 6500])
            runner, host = await serve(dev.app())
            b = bulb("shelly", "cc", **await be.add({"host": host}))
            await be.apply(b, 70)
            assert await be.apply_color(b, {"mode": "hs", "hue": 10, "sat": 10}) is False
            assert await be.apply_color(b, {"mode": "ct", "kelvin": 4321})
            assert dev.calls[-1] == ("CCT.Set", {"id": 0, "ct": 4321})
            await runner.cleanup()
        finally:
            await be.stop()
    run(main())


def test_gen2_older_config_is_a_light_and_learns_no_colour():
    async def main():
        dev = FakeGen2()
        runner, host = await serve(dev.app())
        be = ShellyBackend(FakeCtx())
        b = bulb("shelly", "old", host=host, gen=2, channel=0)
        try:
            await be._probe(b)
            assert b.config["color"] == [] and "component" not in b.config
            await be.apply(b, 20)
            assert dev.calls[-1] == ("Light.Set", {"id": 0, "on": True, "brightness": 20})
            assert await be.apply_color(b, {"mode": "hs", "hue": 0, "sat": 100}) is False
        finally:
            await be.stop()
            await runner.cleanup()
    run(main())
