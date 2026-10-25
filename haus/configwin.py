"""`hauslicht config`: the Plasma widget's Configure pages in a window of their own.

The pages (Bulbs, Profiles, Sound, Video, Notifications) live in the widget's
package, Haus.Licht-plasma. Its contents/standalone/main.qml hosts them in a
window, run by Qt's own qml runtime with contents/standalone/imports first on
the import path: small stand-ins for the two Plasma-only modules the pages
import. Kirigami, KCMUtils, KIconThemes and Plasma5Support are the system's
real QML modules, so no Plasma session is needed (docs/settings-window.md).
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .common import _

PACKAGE_ID = "haus.licht.tray"
SYSTEM_PLASMOIDS = Path("/usr/share/plasma/plasmoids")
HOST = Path("contents", "standalone", "main.qml")
IMPORTS = Path("contents", "standalone", "imports")
# The window's app id on Wayland is Qt's organisation domain reversed (main.qml
# sets gelaende51.github.io) plus the name the runtime was started under, so
# the runtime runs through a link named RUNNER: io.github.gelaende51.HausLicht,
# Haus.Licht's desktop file, whose icon and name the compositor then shows.
RUNNER = "HausLicht"


def package_dirs(env: dict[str, str] | None = None, home: Path | None = None) -> list[Path]:
    """Where the widget's package may be, in the order they are tried."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    dirs = [Path(env["HAUSLICHT_PLASMOID"]).expanduser()] if env.get("HAUSLICHT_PLASMOID") else []
    return dirs + [home / ".local/share/plasma/plasmoids" / PACKAGE_ID,
                   SYSTEM_PLASMOIDS / PACKAGE_ID,
                   home / ".local/share/hauslicht/plasmoid"]


def find_package(env: dict[str, str] | None = None, home: Path | None = None) -> Path | None:
    """The first package that has the standalone host; a widget older than
    the host (no contents/standalone) is passed over."""
    for d in package_dirs(env, home):
        if (d / HOST).is_file():
            return d
    return None


# --------------------------------------------------------------- runtime

def qml_runtime(env: dict[str, str] | None = None) -> str | None:
    """Qt 6's qml tool (Arch qml6, Fedora qml-qt6, else in Qt's bin directory)."""
    path = (os.environ if env is None else env).get("PATH")
    for name in ("qml6", "qml-qt6"):
        found = shutil.which(name, path=path)
        if found:
            return found
    for pattern in ("/usr/lib/qt6/bin/qml", "/usr/lib64/qt6/bin/qml", "/usr/lib/*/qt6/bin/qml"):
        for cand in sorted(glob.glob(pattern)):
            if os.access(cand, os.X_OK):
                return cand
    return None


def qml_dirs(env: dict[str, str] | None = None) -> list[Path]:
    """Qt 6's directories of QML modules ("qml") and of plugins ("plugins"):
    the path variables first, then the usual system places."""
    env = os.environ if env is None else env
    out: dict[str, list[Path]] = {}
    for kind, variables in (("qml", ("QML_IMPORT_PATH", "QML2_IMPORT_PATH")), ("plugins", ("QT_PLUGIN_PATH",))):
        dirs = [Path(p) for var in variables for p in env.get(var, "").split(os.pathsep) if p]
        for pattern in (f"/usr/lib/qt6/{kind}", f"/usr/lib64/qt6/{kind}", f"/usr/lib/*/qt6/{kind}"):
            dirs += [Path(p) for p in sorted(glob.glob(pattern))]
        out[kind] = [d for d in dirs if d.is_dir()]
    return out


# What the pages need, and the packages that bring it: (name, files that
# must be there as "qml:…" or "plugins:…" relative to qml_dirs(),
# {distribution: packages}). The qml runtime is checked on its own; it is the
# first entry. The executable data engine runs hauslicht-ctl for the pages.
NEEDS: list[tuple[str, list[str], dict[str, list[str]]]] = [
    ("qml runtime (Qt 6)", [], {
        "arch": ["qt6-declarative"], "debian": ["qml-qt6"],
        "fedora": ["qt6-qtdeclarative-devel"], "opensuse": ["qt6-declarative-tools"]}),
    ("Qt Quick Controls, Layouts, Dialogs", [f"qml:{m}/qmldir" for m in (
            "QtCore", "QtQuick/Controls", "QtQuick/Layouts", "QtQuick/Dialogs")], {
        "arch": ["qt6-declarative"],
        "debian": ["qml6-module-qtcore", "qml6-module-qtquick-controls", "qml6-module-qtquick-layouts",
                   "qml6-module-qtquick-dialogs"],
        "fedora": ["qt6-qtdeclarative"], "opensuse": ["qt6-declarative-imports"]}),
    ("Kirigami", ["qml:org/kde/kirigami/qmldir"], {
        "arch": ["kirigami"], "debian": ["qml6-module-org-kde-kirigami"],
        "fedora": ["kf6-kirigami"], "opensuse": ["kf6-kirigami-imports"]}),
    ("KCMUtils", ["qml:org/kde/kcmutils/qmldir"], {
        "arch": ["kcmutils"], "debian": ["qml6-module-org-kde-kcmutils"],
        "fedora": ["kf6-kcmutils"], "opensuse": ["kf6-kcmutils-imports"]}),
    ("KIconThemes", ["qml:org/kde/iconthemes/qmldir"], {
        "arch": ["kiconthemes"], "debian": ["qml6-module-org-kde-iconthemes"],
        "fedora": ["kf6-kiconthemes"], "opensuse": ["kf6-kiconthemes-imports"]}),
    ("Plasma5Support", ["qml:org/kde/plasma/plasma5support/qmldir",
                        "plugins:plasma5support/dataengine/plasma_engine_executable.so"], {
        "arch": ["plasma5support"], "debian": ["qml6-module-org-kde-plasma-plasma5support", "libplasma5support6"],
        "fedora": ["plasma5support"], "opensuse": ["plasma5support6"]}),
]
INSTALL = [("Arch", "arch", "sudo pacman -S"), ("Debian/Ubuntu", "debian", "sudo apt install"),
           ("Fedora", "fedora", "sudo dnf install"), ("openSUSE", "opensuse", "sudo zypper install")]


def missing(qml: str | None, dirs: dict[str, list[Path]]) -> list[int]:
    """Indexes into NEEDS of what is not installed. A kind of directory not
    found at all (an unusual layout) is not judged: the window then tells."""
    out = [] if qml else [0]
    for i, (_name, files, _pk) in enumerate(NEEDS):
        for f in files:
            kind, rel = f.split(":", 1)
            if dirs.get(kind) and not any((d / rel).is_file() for d in dirs[kind]):
                out.append(i)
                break
    return out


def missing_message(indexes: list[int]) -> str:
    """Which runtime parts are missing, and the packages per distribution."""
    lines = [_("hauslicht config needs: {parts}").format(parts=", ".join(NEEDS[i][0] for i in indexes)),
             _("Install them with:")]
    for label, key, command in INSTALL:
        pkgs = list(dict.fromkeys(p for i in indexes for p in NEEDS[i][2][key]))
        lines.append(f"  {label + ':':<15}{command} {' '.join(pkgs)}")
    if any(NEEDS[i][0] == "Plasma5Support" for i in indexes):
        # Debian 13 still ships the executable engine in plasma-workspace (sid: libplasma5support6)
        lines.append(_("Older Debian and Ubuntu releases have the executable engine in plasma-workspace."))
    return "\n".join(lines)


def not_found_message(env: dict[str, str] | None = None, home: Path | None = None) -> str:
    lines = [_("hauslicht config: the widget's pages were not found (a Haus.Licht-plasma package with "
               "contents/standalone/main.qml). Looked in:")]
    lines += [f"  {d}" for d in package_dirs(env, home)]
    lines.append(_("install/install.sh fetches them; or point HAUSLICHT_PLASMOID at a package directory."))
    return "\n".join(lines)


def launch_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """The window's environment: Qt messages to its log rather than the
    journal; Qt's own window icon (the qml runtime's) not sent to Wayland
    compositors, so they take the icon from the desktop file."""
    out = dict(os.environ if env is None else env)
    out["QT_FORCE_STDERR_LOGGING"] = "1"
    disabled = [s for s in out.get("QT_WAYLAND_DISABLED_INTERFACES", "").split(",") if s]
    if "xdg_toplevel_icon_manager_v1" not in disabled:
        disabled.append("xdg_toplevel_icon_manager_v1")
    out["QT_WAYLAND_DISABLED_INTERFACES"] = ",".join(disabled)
    return out


def log_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "hauslicht" / "config-window.log"


def runner(qml: str) -> str:
    """~/.cache/hauslicht/HausLicht, a link to the qml runtime (see RUNNER);
    the runtime itself when the link cannot be made."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    link = Path(base) / "hauslicht" / RUNNER
    try:
        if not (link.is_symlink() and os.readlink(link) == qml):
            link.parent.mkdir(parents=True, exist_ok=True)
            tmp = link.with_name(f".{RUNNER}.{os.getpid()}")
            tmp.unlink(missing_ok=True)
            tmp.symlink_to(qml)
            tmp.replace(link)
        return str(link)
    except OSError:
        return qml


def run() -> int:
    """Open the window, detached from this shell. A window that exits within
    the first seconds (a QML error) has its log printed instead."""
    package = find_package()
    if package is None:
        print(not_found_message(), file=sys.stderr)
        return 1
    qml = qml_runtime()
    gaps = missing(qml, qml_dirs())
    if gaps:
        print(missing_message(gaps), file=sys.stderr)
        return 1
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w") as out:
        proc = subprocess.Popen([runner(qml), "-I", str(package / IMPORTS), str(package / HOST)],
                                env=launch_env(), start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            if proc.returncode != 0:
                sys.stderr.write(log.read_text(errors="replace"))
                print(_("hauslicht config: the window did not open (exit {code}); log: {log}").format(
                    code=proc.returncode, log=log), file=sys.stderr)
                return 1
            return 0
        time.sleep(0.1)
    return 0
