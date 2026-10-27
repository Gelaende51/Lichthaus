"""hauslicht config: where the widget's pages are looked for, and what it says
when the QML runtime or a module is missing (haus/configwin.py)."""

from pathlib import Path

import pytest

from haus import configwin
from haus.common import GERMAN


@pytest.fixture(autouse=True)
def english(monkeypatch, tmp_path):
    monkeypatch.setattr(configwin, "_", lambda s: s)  # the language is fixed at import
    monkeypatch.setattr(configwin, "SYSTEM_PLASMOIDS", tmp_path / "usr-share-plasmoids")


def package(d: Path) -> Path:
    (d / configwin.HOST).parent.mkdir(parents=True)
    (d / configwin.HOST).write_text("import QtQuick\n")
    return d


def test_lookup_order(tmp_path):
    home = tmp_path / "home"
    env = {"HAUSLICHT_PLASMOID": str(tmp_path / "checkout")}
    assert configwin.package_dirs(env, home) == [
        tmp_path / "checkout",
        home / ".local/share/plasma/plasmoids/haus.licht.tray",
        tmp_path / "usr-share-plasmoids/haus.licht.tray",
        home / ".local/share/hauslicht/plasmoid",
    ]
    assert configwin.package_dirs({}, home)[0] == home / ".local/share/plasma/plasmoids/haus.licht.tray"


def test_first_package_with_the_host_wins(tmp_path):
    home = tmp_path / "home"
    places = configwin.package_dirs({"HAUSLICHT_PLASMOID": str(tmp_path / "checkout")}, home)
    assert configwin.find_package({}, home) is None
    package(places[3])
    assert configwin.find_package({}, home) == places[3]
    package(places[2])
    assert configwin.find_package({}, home) == places[2]
    # a widget older than the host (no contents/standalone) is passed over
    (places[1] / "contents" / "ui").mkdir(parents=True)
    assert configwin.find_package({}, home) == places[2]
    package(places[1])
    assert configwin.find_package({}, home) == places[1]
    package(places[0])
    assert configwin.find_package({"HAUSLICHT_PLASMOID": str(places[0])}, home) == places[0]


def test_not_found_names_every_place(tmp_path):
    home = tmp_path / "home"
    text = configwin.not_found_message({}, home)
    for d in configwin.package_dirs({}, home):
        assert str(d) in text
    assert "HAUSLICHT_PLASMOID" in text


def roots(tmp_path, have: list[str]) -> dict[str, list[Path]]:
    out = {"qml": [tmp_path / "qml"], "plugins": [tmp_path / "plugins"]}
    for f in have:
        kind, rel = f.split(":", 1)
        path = out[kind][0] / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    return out


ALL = [f for _name, files, _pk in configwin.NEEDS for f in files]


def test_nothing_missing(tmp_path):
    assert configwin.missing("/usr/bin/qml6", roots(tmp_path, ALL)) == []


def test_missing_runtime_and_modules(tmp_path):
    have = [f for f in ALL if "kirigami" not in f and "plasma_engine_executable" not in f]
    gaps = configwin.missing(None, roots(tmp_path, have))
    assert [configwin.NEEDS[i][0] for i in gaps] == ["qml runtime (Qt 6)", "Kirigami", "Plasma5Support"]
    text = configwin.missing_message(gaps)
    assert "hauslicht config needs: qml runtime (Qt 6), Kirigami, Plasma5Support" in text
    assert "sudo pacman -S qt6-declarative kirigami plasma5support" in text
    assert ("sudo apt install qml-qt6 qml6-module-org-kde-kirigami qml6-module-org-kde-plasma-plasma5support "
            "libplasma5support6") in text
    assert "sudo dnf install qt6-qtdeclarative-devel kf6-kirigami plasma5support" in text
    assert "sudo zypper install qt6-declarative-tools kf6-kirigami-imports plasma5support6" in text
    assert "plasma-workspace" in text


def test_unknown_layout_is_not_judged(tmp_path):
    # no QML or plugin directory found at all: only the runtime is checked
    assert configwin.missing("/usr/bin/qml6", {"qml": [], "plugins": []}) == []
    assert configwin.missing(None, {"qml": [], "plugins": []}) == [0]


def test_qml_runtime_names(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    qml = bin_ / "qml-qt6"
    qml.write_text("#!/bin/sh\n")
    qml.chmod(0o755)
    assert configwin.qml_runtime({"PATH": str(bin_)}) == str(qml)


def test_launch_env_keeps_other_disabled_interfaces():
    env = configwin.launch_env({"QT_WAYLAND_DISABLED_INTERFACES": "wp_fractional_scale_manager_v1"})
    assert env["QT_WAYLAND_DISABLED_INTERFACES"] == "wp_fractional_scale_manager_v1,xdg_toplevel_icon_manager_v1"
    assert configwin.launch_env({})["QT_WAYLAND_DISABLED_INTERFACES"] == "xdg_toplevel_icon_manager_v1"


def test_messages_have_german():
    for text in ("hauslicht config needs: {parts}", "Install them with:",
                 "Older Debian and Ubuntu releases have the executable engine in plasma-workspace.",
                 "install/install.sh fetches them; or point HAUSLICHT_PLASMOID at a package directory.",
                 "hauslicht config: the window did not open (exit {code}); log: {log}"):
        assert text in GERMAN


# ------------------------------------------------------ System Settings

def kcm(root: Path) -> Path:
    f = root / configwin.KCM_NAMESPACE / f"{configwin.KCM_FIRST}.so"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("")
    return f


@pytest.fixture
def system(monkeypatch, tmp_path):
    """Qt's own plugin directory is tmp_path/sys-plugins; systemsettings is on
    PATH; no System Settings is open."""
    sysdir = tmp_path / "sys-plugins"
    sysdir.mkdir()

    def dirs(env=None):
        env = {} if env is None else env
        named = [Path(p) for p in env.get("QT_PLUGIN_PATH", "").split(":") if p]
        return {"qml": [], "plugins": [d for d in named + [sysdir] if d.is_dir()]}
    monkeypatch.setattr(configwin, "qml_dirs", dirs)
    monkeypatch.setattr(configwin, "systemsettings_running", lambda: False)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "systemsettings").write_text("#!/bin/sh\n")
    (bin_ / "systemsettings").chmod(0o755)
    return {"sys": sysdir, "home": tmp_path / "home",
            "env": {"XDG_CURRENT_DESKTOP": "KDE", "PATH": str(bin_)}}


def test_kcm_found_per_user_or_where_qt_looks(system):
    home, env = system["home"], system["env"]
    assert configwin.kcm_plugin(env, home) is None
    user = kcm(home / configwin.USER_PLUGINS)
    assert configwin.kcm_plugin(env, home) == user
    # Qt's own directory (a distribution package) comes first
    sysfile = kcm(system["sys"])
    assert configwin.kcm_plugin(env, home) == sysfile


def test_system_settings_gets_the_user_plugin_dir(system):
    home, env = system["home"], system["env"]
    user = kcm(home / configwin.USER_PLUGINS)
    argv, run_env = configwin.system_settings(add_bulb=True, env=env, home=home)
    assert argv[1:] == ["kcm_hauslicht_bulbs", "--args", "add"]
    assert run_env["QT_PLUGIN_PATH"] == str(home / configwin.USER_PLUGINS)
    # a session that names it already (the env script): unchanged
    named = {**env, "QT_PLUGIN_PATH": f"/opt/plugins:{home / configwin.USER_PLUGINS}"}
    argv, run_env = configwin.system_settings(env=named, home=home)
    assert argv[1:] == ["kcm_hauslicht_bulbs"] and run_env["QT_PLUGIN_PATH"] == named["QT_PLUGIN_PATH"]
    # installed system-wide: nothing to add
    user.unlink()
    kcm(system["sys"])
    _argv, run_env = configwin.system_settings(env=env, home=home)
    assert "QT_PLUGIN_PATH" not in run_env


def test_no_system_settings_without_plasma_systemsettings_or_kcm(system, monkeypatch):
    home, env = system["home"], system["env"]
    assert configwin.system_settings(env=env, home=home) == (
        "Haus.Licht's pages are not installed in System Settings (Haus.Licht-plasma's kcm/)")
    kcm(home / configwin.USER_PLUGINS)
    assert configwin.system_settings(env={**env, "XDG_CURRENT_DESKTOP": "GNOME"}, home=home) == "not a Plasma session"
    assert configwin.system_settings(env={**env, "PATH": str(home)}, home=home) == (
        "System Settings (systemsettings) is not installed")
    # an open System Settings started before the env script: it cannot see them
    monkeypatch.setattr(configwin, "systemsettings_running", lambda: True)
    assert "after the next login" in configwin.system_settings(env=env, home=home)
    named = {**env, "QT_PLUGIN_PATH": str(home / configwin.USER_PLUGINS)}
    assert not isinstance(configwin.system_settings(env=named, home=home), str)


def test_run_prefers_system_settings_and_window_on_request(monkeypatch):
    started, windows = [], []
    monkeypatch.setattr(configwin.subprocess, "Popen", lambda argv, **kw: started.append((argv, kw["env"])))
    monkeypatch.setattr(configwin, "run_window", lambda: windows.append(1) or 0)
    monkeypatch.setattr(configwin, "system_settings",
                        lambda add_bulb=False: (["systemsettings", "kcm_hauslicht_bulbs"] + (["--args", "add"] if add_bulb else []), {"A": "1"}))
    assert configwin.run(add_bulb=True) == 0
    assert started == [(["systemsettings", "kcm_hauslicht_bulbs", "--args", "add"], {"A": "1"})] and windows == []
    assert configwin.run(window=True) == 0
    assert len(started) == 1 and windows == [1]


def test_system_settings_only_opens_nothing_and_exits_3(monkeypatch, capsys):
    monkeypatch.setattr(configwin, "system_settings", lambda add_bulb=False: "not a Plasma session")
    monkeypatch.setattr(configwin, "run_window", lambda: pytest.fail("the window opened"))
    monkeypatch.setattr(configwin.subprocess, "Popen", lambda *a, **kw: pytest.fail("something started"))
    assert configwin.run(system_settings_only=True, add_bulb=True) == configwin.NO_SYSTEM_SETTINGS == 3
    assert "not a Plasma session" in capsys.readouterr().err


def test_system_settings_messages_have_german():
    for text in ("not a Plasma session", "System Settings (systemsettings) is not installed",
                 "Haus.Licht's pages are not installed in System Settings (Haus.Licht-plasma's kcm/)",
                 "System Settings is open without Haus.Licht's pages; they show there after the next login",
                 "the settings window, also on Plasma",
                 "only System Settings: where Haus.Licht's pages are not there, open nothing and exit with 3",
                 "System Settings: the Bulbs page with its Add dialog"):
        assert text in GERMAN
