"""The migration from the old name Haus.Licht (dev/rename-lichthaus.md).

rename-lichthaus: keep-file (these tests are about the old names)

Daemon side: the state file and its backups move by os.replace, never over
a newer file and never while a process of the old name holds the old lock;
the root helpers come from the old directory until setup-root.sh stamped
the new one. Install side: install/migrate-rename.sh against a temporary
HOME, with stand-ins for systemctl, kpackagetool6 and pgrep that only log.
The state files here are fakes written by the tests.
"""

import fcntl
import os
import subprocess
from pathlib import Path

import pytest

from haus import common, rootsetup

ROOT = Path(__file__).resolve().parent.parent
MIGRATE = ROOT / "install" / "migrate-rename.sh"
FAKE = '{"fake": "state written by the test"}'


# ------------------------------------------------------------ the state file

def test_state_file_backups_and_armed_file_move_by_rename(tmp_path):
    old = tmp_path / "hauslicht.json"
    old.write_text(FAKE)
    (tmp_path / "hauslicht.json.bak-pre-multibulb").write_text("backup")
    (tmp_path / "hauslicht.armed.json").write_text('{"bulbs": {}}')
    (tmp_path / "hauslicht.lock").write_text("")
    inode = old.stat().st_ino
    moved = common.migrate_state_files(tmp_path)
    assert moved == [("hauslicht.json", "lichthaus.json"),
                     ("hauslicht.armed.json", "lichthaus.armed.json"),
                     ("hauslicht.json.bak-pre-multibulb", "lichthaus.json.bak-pre-multibulb")]
    new = tmp_path / "lichthaus.json"
    assert new.stat().st_ino == inode  # the same file: moved, not copied
    assert new.read_text() == FAKE
    assert (tmp_path / "lichthaus.json.bak-pre-multibulb").read_text() == "backup"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "lichthaus.armed.json", "lichthaus.json", "lichthaus.json.bak-pre-multibulb"]  # old lock gone too
    assert common.migrate_state_files(tmp_path) == []  # a second start: nothing to do


def test_nothing_moves_when_the_new_state_file_exists(tmp_path):
    (tmp_path / "hauslicht.json").write_text("old")
    (tmp_path / "hauslicht.json.bak1").write_text("old backup")
    (tmp_path / "lichthaus.json").write_text("new")
    assert common.migrate_state_files(tmp_path) == []
    assert (tmp_path / "hauslicht.json").read_text() == "old"
    assert (tmp_path / "lichthaus.json").read_text() == "new"
    assert not (tmp_path / "lichthaus.json.bak1").exists()


def test_a_taken_backup_name_is_not_overwritten(tmp_path):
    (tmp_path / "hauslicht.json").write_text(FAKE)
    (tmp_path / "hauslicht.json.bak1").write_text("old backup")
    (tmp_path / "hauslicht.json.bak2").write_text("old backup 2")
    (tmp_path / "lichthaus.json.bak1").write_text("new backup")
    moved = common.migrate_state_files(tmp_path)
    assert ("hauslicht.json.bak1", "lichthaus.json.bak1") not in moved
    assert ("hauslicht.json.bak2", "lichthaus.json.bak2") in moved
    assert (tmp_path / "lichthaus.json.bak1").read_text() == "new backup"
    assert (tmp_path / "hauslicht.json.bak1").read_text() == "old backup"


def test_nothing_moves_while_the_old_lock_is_held(tmp_path):
    (tmp_path / "hauslicht.json").write_text(FAKE)
    lock = tmp_path / "hauslicht.lock"
    lock.write_text("")
    fd = os.open(lock, os.O_RDWR)  # a daemon of the old name, still running
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert common.migrate_state_files(tmp_path) == []
        assert (tmp_path / "hauslicht.json").exists() and not (tmp_path / "lichthaus.json").exists()
    finally:
        os.close(fd)
    assert common.migrate_state_files(tmp_path)[0] == ("hauslicht.json", "lichthaus.json")


def test_a_fresh_install_has_nothing_to_move(tmp_path):
    assert common.migrate_state_files(tmp_path) == []
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------ root helpers

@pytest.fixture
def helper_dirs(tmp_path, monkeypatch):
    new, old = tmp_path / "lichthaus", tmp_path / "hauslicht"
    monkeypatch.setattr(common, "HELPER_DIR", new)
    monkeypatch.setattr(common, "OLD_HELPER_DIR", old)
    return new, old


def test_helpers_from_the_old_directory_until_setup_root_stamped_the_new(helper_dirs):
    new, old = helper_dirs
    assert common.helper_dir() == new  # neither: a fresh install
    old.mkdir()
    (old / "setup-version").write_text("6\n")
    assert common.helper_dir() == old
    assert common.helper_path(str(new / "hci-claim")) == str(old / "hci-claim")
    assert rootsetup.installed() == 6  # the old stamp counts until then
    new.mkdir()
    (new / "conn-update").write_text("")  # the older Licht.Haus left this, without a stamp
    assert common.helper_dir() == old
    (new / "setup-version").write_text("7\n")
    assert common.helper_dir() == new
    assert common.helper_path(str(new / "hci-claim")) == str(new / "hci-claim")
    assert rootsetup.installed() == 7


def test_other_helper_paths_stay_as_they_are(helper_dirs):
    _new, old = helper_dirs
    old.mkdir()
    assert common.helper_path("/nonexistent/hci-claim") == "/nonexistent/hci-claim"


def test_setup_is_outdated_while_the_helpers_have_only_the_old_name(helper_dirs, tmp_path, monkeypatch):
    new, old = helper_dirs
    version = tmp_path / "setup-version"
    version.write_text("6\n")
    monkeypatch.setattr(rootsetup, "VERSION_FILE", version)
    old.mkdir()
    (old / "setup-version").write_text("6\n")  # current by its number
    assert rootsetup.status() == {"status": "outdated", "installed": 6, "required": 6}
    new.mkdir()
    (new / "conn-update").write_text("")  # Licht.Haus's leftover: still the old helpers
    assert rootsetup.status()["status"] == "outdated"
    (new / "setup-version").write_text("6\n")  # setup-root.sh ran again
    assert rootsetup.status() == {"status": "current", "installed": 6, "required": 6}
    (new / "setup-version").unlink()
    (old / "setup-version").unlink()
    old.rmdir()
    assert rootsetup.status(legacy=[])["status"] == "missing"  # a fresh install


def test_setup_root_removes_what_the_old_name_installed():
    script = (ROOT / "install" / "setup-root.sh").read_text()
    for path in ("/etc/modules-load.d/haus-licht.conf", "/etc/udev/rules.d/70-haus-licht.rules",
                 "/etc/udev/rules.d/70-haus-licht-devices.rules", "/etc/sudoers.d/hauslicht",
                 "/usr/local/lib/hauslicht/setup-version"):
        assert path in script
    # the cleanup comes before the new stamp: the daemon switches only once both are done
    assert script.index("/etc/sudoers.d/hauslicht") < script.index("/usr/local/lib/lichthaus/setup-version.new")
    assert 'mv "$conf.hauslicht-bak" "$conf.lichthaus-bak"' in script


# ------------------------------------------------------------ install/migrate-rename.sh

STUB = """#!/bin/sh
rc="$HOME/.config/plasma-org.kde.plasma.desktop-appletsrc"
echo "$(basename "$0") $* [old ids: $(grep -c 'haus\\.licht\\.tray' "$rc" 2>/dev/null)]" >> "$STUB_LOG"
case "$(basename "$0") $*" in
    *is-active*plasma-plasmashell*) exit "${STUB_PLASMA_ACTIVE:-1}" ;;
    *is-enabled*) exit 0 ;;
    pgrep*) exit "${STUB_PLASMA_RUNNING:-1}" ;;
    kpackagetool6*) exit 1 ;;
esac
exit 0
"""

APPLETSRC = """[Containments][1][Applets][5]
immutability=1
plugin=haus.licht.tray

[Containments][1][Applets][5][Configuration][General]
widgetIndex=2

[Containments][8][Applets][30]
plugin=haus.licht.tray

[Containments][8][General]
extraItems=org.kde.plasma.battery,haus.licht.tray,org.kde.plasma.volume
knownItems=haus.licht.tray,org.kde.plasma.battery
shownItems=haus.licht.tray
comment=haus.licht.tray elsewhere stays
"""


class Home:
    def __init__(self, tmp_path):
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.checkout = tmp_path / "checkout"
        self.checkout.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name in ("systemctl", "kpackagetool6", "pgrep", "plasmashell", "kquitapp6", "kstart"):
            (self.bin / name).write_text(STUB)
            (self.bin / name).chmod(0o755)
        self.log = tmp_path / "calls"
        self.units = self.home / ".config/systemd/user"

    def path(self, rel: str) -> Path:
        return self.home / rel

    def write(self, rel: str, text: str = "") -> Path:
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def link(self, rel: str, target: str) -> Path:
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.symlink_to(target)
        return p

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        self.log.write_text("")
        clean = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
        clean.update(HOME=str(self.home), PATH=f"{self.bin}:/usr/bin:/bin", STUB_LOG=str(self.log), LC_ALL="C", **env)
        proc = subprocess.run(["sh", str(MIGRATE), *args], env=clean, capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        return proc

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()


@pytest.fixture
def home(tmp_path):
    return Home(tmp_path)


def old_install(h: Home) -> None:
    h.write(".config/systemd/user/hauslicht.service", "[Service]\nExecStart=/x/hauslicht.py sync\n")
    h.link(".config/systemd/user/graphical-session.target.wants/hauslicht.service", "../hauslicht.service")
    h.write(".config/systemd/user/hauslicht.service.d/debug.conf", "[Service]\nEnvironment=HAUSLICHT_DEBUG=1\n")
    # the older Licht.Haus: its unit runs another checkout, with a drop-in of its own
    h.write(".config/systemd/user/lichthaus.service",
            "[Service]\nExecStart=/x/Licht.Haus/.venv/bin/python /x/Licht.Haus/lichthaus.py sync\n")
    h.write(".config/systemd/user/lichthaus.service.d/debug.conf", "[Service]\nEnvironment=OLD=1\n")
    h.write(".config/hauslicht/settings", "kept")
    h.write(".cache/hauslicht/model.tflite", "model")
    for rel in ("bin/hauslicht", "bin/hauslicht-ctl", "share/man/man1/hauslicht.1",
                "share/man/man1/hauslicht-ctl.1", "share/man/de/man1/hauslicht.1", "share/man/de/man1/hauslicht-ctl.1"):
        h.link(".local/" + rel, "/x/checkout/" + rel.rsplit("/", 1)[1])
    h.write(".local/bin/hauslicht-mine", "not ours")
    h.write(".local/share/applications/io.github.gelaende51.HausLicht.desktop")
    h.write(".local/share/systemsettings/categories/settings-hardware-hauslicht.desktop")
    h.write(".config/plasma-workspace/env/hauslicht-kcm.sh")
    h.write(".local/share/plasma/systemsettings/externalmodules/hauslicht-systemsettings.desktop")
    h.write(".local/lib/qt6/plugins/plasma/kcms/systemsettings_qwidgets/kcm_hauslicht_bulbs.so")
    h.write(".local/lib/qt6/plugins/plasma/kcms/systemsettings_qwidgets/kcm_other.so")
    h.write(".local/share/plasma/plasmoids/haus.licht.tray/metadata.json", "{}")
    h.write(".local/share/hauslicht/plasmoid/contents/standalone/main.qml")


def test_before_moves_an_old_install_to_the_new_names(home):
    h = home
    old_install(h)
    out = h.run("before", str(h.checkout)).stdout
    calls = h.calls()
    assert [c.split(" [")[0] for c in calls if c.startswith("systemctl")] == [
        "systemctl --user stop lichthaus.service", "systemctl --user disable lichthaus.service",
        "systemctl --user is-enabled --quiet hauslicht.service",
        "systemctl --user stop hauslicht.service", "systemctl --user disable hauslicht.service"]
    assert "kpackagetool6 --type Plasma/Applet --remove haus.licht.tray" in calls[-1]
    assert "enable the new one" in out
    units = h.units
    assert not (units / "hauslicht.service").exists()
    assert not (units / "graphical-session.target.wants/hauslicht.service").is_symlink()
    assert not (units / "hauslicht.service.d").exists()
    assert (units / "lichthaus.service.d/debug.conf").read_text() == "[Service]\nEnvironment=LICHTHAUS_DEBUG=1\n"
    assert (units / "lichthaus.service.d.old-licht-haus/debug.conf").read_text() == "[Service]\nEnvironment=OLD=1\n"
    assert "set aside as lichthaus.service.d.old-licht-haus" in out
    assert (units / "lichthaus.service").exists()  # install.sh replaces it
    assert h.path(".config/lichthaus/settings").read_text() == "kept"
    assert not h.path(".config/hauslicht").exists()
    assert h.path(".cache/lichthaus/model.tflite").read_text() == "model"
    left = sorted(str(p.relative_to(h.home)) for p in h.home.rglob("*") if "hauslicht" in p.name or "HausLicht" in p.name
                  or "haus.licht" in p.name)
    assert left == [".local/bin/hauslicht-mine"]  # a file of the user's own stays
    assert h.path(".local/lib/qt6/plugins/plasma/kcms/systemsettings_qwidgets/kcm_other.so").exists()


def test_before_twice_changes_nothing(home):
    h = home
    old_install(h)
    h.run("before", str(h.checkout))
    snapshot = sorted((str(p), p.is_symlink()) for p in h.home.rglob("*"))
    proc = h.run("before", str(h.checkout))
    assert proc.stdout == "" and h.calls() == []  # the new unit is foreign still, but nothing to set aside
    assert sorted((str(p), p.is_symlink()) for p in h.home.rglob("*")) == snapshot


def test_before_keeps_our_own_drop_in(home):
    h = home
    h.write(".config/systemd/user/lichthaus.service",
            f"[Service]\nExecStart={h.checkout}/.venv/bin/python {h.checkout}/lichthaus.py sync\n")
    h.write(".config/systemd/user/lichthaus.service.d/debug.conf", "[Service]\nEnvironment=LICHTHAUS_DEBUG=1\n")
    proc = h.run("before", str(h.checkout))
    assert proc.stdout == "" and h.calls() == []
    assert (h.units / "lichthaus.service.d/debug.conf").exists()


def test_panel_rewritten_while_plasmashell_is_stopped(home):
    h = home
    rc = h.write(".config/plasma-org.kde.plasma.desktop-appletsrc", APPLETSRC)
    out = h.run("panel", STUB_PLASMA_ACTIVE="0").stdout
    assert h.calls() == [
        "systemctl --user is-active --quiet plasma-plasmashell.service [old ids: 6]",
        "systemctl --user stop plasma-plasmashell.service [old ids: 6]",  # still the old file when it stops
        "systemctl --user start plasma-plasmashell.service [old ids: 1]"]  # the new one when it starts
    assert rc.read_text() == APPLETSRC.replace("plugin=haus.licht.tray", "plugin=lichthaus.tray").replace(
        "battery,haus.licht.tray,", "battery,lichthaus.tray,").replace(
        "knownItems=haus.licht.tray,", "knownItems=lichthaus.tray,").replace(
        "shownItems=haus.licht.tray", "shownItems=lichthaus.tray")
    assert h.path(".config/plasma-org.kde.plasma.desktop-appletsrc.hauslicht-bak").read_text() == APPLETSRC
    assert "backup: plasma-org.kde.plasma.desktop-appletsrc.hauslicht-bak" in out
    proc = h.run("panel", STUB_PLASMA_ACTIVE="0")  # twice: nothing left to do
    assert proc.stdout == "" and h.calls() == []
    assert len(list(h.path(".config").glob("plasma-org.kde.plasma.desktop-appletsrc.*"))) == 1


def test_panel_left_alone_while_plasmashell_runs_outside_systemd(home):
    h = home
    rc = h.write(".config/plasma-org.kde.plasma.desktop-appletsrc", APPLETSRC)
    proc = h.run("panel", STUB_PLASMA_ACTIVE="3", STUB_PLASMA_RUNNING="0")
    assert rc.read_text() == APPLETSRC
    assert "remove the old widget from the panel" in proc.stderr
    assert not any("stop" in c for c in h.calls())
    assert list(h.path(".config").glob("*-bak*")) == []


def test_panel_rewritten_when_plasmashell_does_not_run(home):
    h = home
    rc = h.write(".config/plasma-org.kde.plasma.desktop-appletsrc", APPLETSRC)
    h.run("panel", STUB_PLASMA_ACTIVE="3", STUB_PLASMA_RUNNING="1")
    assert "plugin=haus.licht.tray" not in rc.read_text()
    assert "comment=haus.licht.tray elsewhere stays" in rc.read_text()


def test_panel_without_plasma(home):
    proc = home.run("panel")
    assert proc.stdout == "" and home.calls() == []


def test_install_sh_runs_the_migration_around_the_new_install():
    script = (ROOT / "install" / "install.sh").read_text()
    before = script.index('migrate-rename.sh" before "$dir"')
    assert script.index('exit 0\nfi', 0) < before < script.index('python3 -m venv')  # never in --dry-run
    assert script.index("fetch_widget \"$tmp\"); then") < script.index('migrate-rename.sh" panel')
