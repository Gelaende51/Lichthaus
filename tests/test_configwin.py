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
