"""A widget's own settings (invert_scroll, icon_set, clap_last,
auto_scan_bluetooth) kept by the daemon per widget, so the Plasma widget and
the settings window show the same: storage and migration, the ctl commands
with their validation, the clap mode carrying clap_last, the CLI, and the
hauslicht-ctl client against a test socket (never the live daemon)."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from haus import clap as clapmod
from haus.common import UserError
from haus.profiles import check_widget_setting, clean_widget_settings, migrate, new_state
from test_cli import with_cli
from test_core import cmd, mock_state, run

ROOT = Path(__file__).resolve().parent.parent


def test_new_state_has_no_widget_settings():
    assert new_state()["settings"]["widget_settings"] == {}


def test_check_widget_setting():
    assert check_widget_setting("invert_scroll", "on") is True
    assert check_widget_setting("auto_scan_bluetooth", "false") is False
    assert check_widget_setting("icon_set", "mdi-bulb") == "mdi-bulb"
    assert check_widget_setting("clap_last", "snap") == "snap"
    for key, value in (("invert_scroll", "maybe"), ("icon_set", ""), ("icon_set", "../../etc"),
                       ("icon_set", "a/b"), ("icon_set", "x" * 65), ("icon_set", ".hidden"),
                       ("clap_last", "off"), ("clap_last", "loud"), ("colour", "red")):
        with pytest.raises(ValueError):
            check_widget_setting(key, value)
    assert check_widget_setting("icon_set", "x" * 64) == "x" * 64


def test_migration_cleans_the_stored_settings():
    state = new_state()
    state["settings"].update(widgets=2, widget_settings={
        "1": {"invert_scroll": True, "icon_set": "../x", "clap_last": "snap", "colour": "red"},
        "2": {"icon_set": 5},  # nothing valid left: as if never written
        "3": {"invert_scroll": True},  # no widget 3
        "x": "junk"})
    migrate(state)
    assert state["settings"]["widget_settings"] == {"1": {"invert_scroll": True, "clap_last": "snap"}}
    state["settings"]["widget_settings"] = "junk"
    migrate(state)
    assert state["settings"]["widget_settings"] == {}
    old = new_state()
    del old["settings"]["widget_settings"]  # a state file from before
    migrate(old)
    assert old["settings"]["widget_settings"] == {}
    assert clean_widget_settings({"1": {"auto_scan_bluetooth": "on"}}, 1) == {"1": {"auto_scan_bluetooth": True}}


def test_ctl_get_and_set_per_widget(tmp_path):
    async def body(d):
        g = await cmd(d, "get")
        assert g["widget_settings"] == {}
        assert await cmd(d, "widget-settings") == {"widget": 1, "widget_settings": {}}
        g = await cmd(d, "widget-settings-set", "invert_scroll=on", "icon_set=mdi-bulb")
        assert g["bulb"] == "sync" and g["widget_settings"] == {"invert_scroll": True, "icon_set": "mdi-bulb"}
        g = await cmd(d, "@1", "widget-settings-set", "auto_scan_bluetooth=off")
        assert g["widget_settings"]["auto_scan_bluetooth"] is False and g["widget_settings"]["invert_scroll"] is True
        saved = json.loads(d.state_path.read_text())["settings"]["widget_settings"]
        assert saved == {"1": {"invert_scroll": True, "icon_set": "mdi-bulb", "auto_scan_bluetooth": False}}

        # unknown widget index: refused, nothing stored
        with pytest.raises(UserError, match=r"\b2\b"):
            await cmd(d, "@2", "widget-settings-set", "invert_scroll=on")
        with pytest.raises(UserError, match=r"\b2\b"):
            await cmd(d, "@2", "widget-settings")
        await cmd(d, "set", "widgets=2")
        assert (await cmd(d, "@2", "get"))["widget_settings"] == {}  # widget 2 has its own
        g = await cmd(d, "@2", "widget-settings-set", "icon_set=tabler-bulb")
        assert g["widget"] == 2 and g["widget_settings"] == {"icon_set": "tabler-bulb"}
        assert (await cmd(d, "get"))["widget_settings"]["icon_set"] == "mdi-bulb"

        # validation: all or nothing
        before = dict(d.settings["widget_settings"]["1"])
        for words in (["invert_scroll=maybe"], ["icon_set=../../x"], ["icon_set=" + "x" * 65],
                      ["clap_last=off"], ["colour=red"], ["invert_scroll"], [],
                      ["invert_scroll=off", "icon_set=a/b"]):
            with pytest.raises(UserError):
                await cmd(d, "widget-settings-set", *words)
        assert d.settings["widget_settings"]["1"] == before

        # fewer widgets: widget 2's settings go with its list
        await cmd(d, "set", "widgets=1")
        assert set(d.settings["widget_settings"]) == {"1"}
    run(tmp_path, mock_state(mock_1={}), body)


def test_settings_survive_a_restart(tmp_path):
    async def first(d):
        await cmd(d, "widget-settings-set", "clap_last=clap", "invert_scroll=on")
        return json.loads(d.state_path.read_text())

    state = run(tmp_path, mock_state(mock_1={}), first)

    async def second(d):
        return (await cmd(d, "get"))["widget_settings"]

    assert run(tmp_path, state, second) == {"clap_last": "clap", "invert_scroll": True}


def test_the_clap_mode_carries_clap_last(tmp_path, monkeypatch):
    monkeypatch.setattr(clapmod.ClapService, "apply", lambda self, mode, source, sensitivity: None)
    monkeypatch.setattr(clapmod, "missing", lambda: [])

    async def body(d):
        await cmd(d, "set", "widgets=2")
        g = await cmd(d, "@2", "clap", "snap")
        assert g["widget_settings"] == {"clap_last": "snap"}
        g = await cmd(d, "@2", "clap", "off")  # off keeps the last mode
        assert g["widget_settings"] == {"clap_last": "snap"}
        assert (await cmd(d, "get"))["widget_settings"] == {}
    run(tmp_path, mock_state(mock_1={}), body)


def test_cli_widget_settings(tmp_path, monkeypatch, capsys):
    async def body(d, hl):
        code, out, err = await hl("widget-settings", "icon_set=mdi-bulb", "invert_scroll=on")
        assert code == 0, err
        assert "icon_set: mdi-bulb" in out and "invert_scroll: yes" in out
        code, out, err = await hl("-w", 1, "widget-settings")
        assert code == 0 and "icon_set: mdi-bulb" in out
        code, out, err = await hl("widget-settings", "clap_last=loud")
        assert code == 1 and "clap_last" in err
        code, out, err = await hl("-w", 3, "widget-settings")
        assert code == 1 and "3" in err
    with_cli(tmp_path, monkeypatch, capsys, mock_state(mock_1={}), body)


def test_hauslicht_ctl_client_against_a_test_socket(tmp_path):
    """The client the widget runs: `@N widget-settings-set` and `get` through
    HAUSLICHT_SOCK pointing at an in-process daemon."""
    sock = tmp_path / "ctl.sock"
    env = {**os.environ, "HAUSLICHT_SOCK": str(sock)}

    def ctl(*argv):
        p = subprocess.run([sys.executable, str(ROOT / "hauslicht-ctl"), *argv], env=env,
                           capture_output=True, text=True, timeout=30)
        return p.returncode, json.loads(p.stdout)

    async def body(d):
        server = await asyncio.start_unix_server(d.handle_client, path=str(sock))
        async with server:
            code, reply = await asyncio.to_thread(ctl, "@1", "widget-settings-set", "icon_set=fluent-bulb")
            assert code == 0 and reply["widget_settings"] == {"icon_set": "fluent-bulb"}
            code, reply = await asyncio.to_thread(ctl, "get")
            assert code == 0 and reply["widget_settings"] == {"icon_set": "fluent-bulb"}
            code, reply = await asyncio.to_thread(ctl, "widget-settings-set", "invert_scroll=sideways")
            assert code == 1 and "invert_scroll" in reply["error"]
            code, reply = await asyncio.to_thread(ctl, "@4", "widget-settings")
            assert code == 1 and "4" in reply["error"]
    run(tmp_path, mock_state(mock_1={}), body)
