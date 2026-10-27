#!/bin/sh
# Per-user install of Haus.Licht from this checkout (no root; for the slider
# also run install/setup-root.sh once with sudo).
#
#   install/install.sh                        mesh bulbs only (Ledvance Volkslicht)
#   install/install.sh --backend wiz,hue      also the libraries these kinds need
#   install/install.sh --all-backends         the libraries of every kind
#   install/install.sh --dry-run ...          print the pip command, change nothing
#   install/install.sh --plasmoid PATH        the tray widget from this .plasmoid file or
#                                             package dir (default: its latest release); the
#                                             package dir of a Haus.Licht-plasma checkout
#                                             also gives its System Settings pages (kcm/)
set -eu
dir=$(cd "$(dirname "$0")/.." && pwd)

kinds=""
dry=""
plasmoid=""
while [ $# -gt 0 ]; do
    case "$1" in
        --backend|--backends) [ $# -ge 2 ] || { echo "$1 needs KIND[,KIND...]" >&2; exit 2; }
            kinds="${kinds:+$kinds,}$2"; shift 2 ;;
        --backend=*|--backends=*) kinds="${kinds:+$kinds,}${1#*=}"; shift ;;
        --all-backends) kinds="all"; shift ;;
        --dry-run|-n) dry=1; shift ;;
        --plasmoid) [ $# -ge 2 ] || { echo "$1 needs a .plasmoid file or a package directory" >&2; exit 2; }
            plasmoid=$2; shift 2 ;;
        --plasmoid=*) plasmoid=${1#*=}; shift ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option $1 (try --help)" >&2; exit 2 ;;
    esac
done
case ",$kinds," in *,all,*) kinds="all" ;; esac
if [ -n "$plasmoid" ] && [ ! -e "$plasmoid" ]; then
    echo "--plasmoid: $plasmoid does not exist" >&2; exit 2
fi

# The Plasma tray widget lives in its own repository; its releases carry
# haus.licht.tray-<version>.plasmoid and a .sha256 beside it.
widget_repo=Gelaende51/Haus.Licht-plasma
widget_api=${HAUSLICHT_WIDGET_API:-https://api.github.com/repos/$widget_repo/releases/latest}

# fetch_widget DIR: download the latest release's .plasmoid and .sha256 into
# DIR, check the sum, print the .plasmoid's path; the release's description
# stays in DIR/release.json (fetch_kcm_source). On failure: the reason on
# stderr, status 1.
fetch_widget() {
    json=$(curl -fsSL --max-time 30 -H "Accept: application/vnd.github+json" "$widget_api" 2>&1) \
        || { echo "the GitHub API did not answer ($json)" >&2; return 1; }
    printf '%s' "$json" > "$1/release.json"
    url=$(printf '%s' "$json" | python3 -c '
import json, sys
try:
    assets = json.load(sys.stdin).get("assets", [])
except ValueError:
    sys.exit(1)
urls = [a.get("browser_download_url", "") for a in assets]
plasmoid = [u for u in urls if u.endswith(".plasmoid") and u + ".sha256" in urls]
if not plasmoid:
    sys.exit(1)
print(plasmoid[0])') \
        || { echo "the latest release has no .plasmoid with a .sha256" >&2; return 1; }
    name=${url##*/}
    { curl -fsSL --max-time 120 -o "$1/$name" "$url" \
        && curl -fsSL --max-time 30 -o "$1/$name.sha256" "$url.sha256"; } \
        || { echo "the download of $name failed" >&2; return 1; }
    (cd "$1" && sha256sum -c --status "$name.sha256") \
        || { echo "$name does not match its .sha256" >&2; return 1; }
    printf '%s\n' "$1/$name"
}

# fetch_kcm_source DIR: the source of the release in DIR/release.json,
# unpacked in DIR/src; prints its kcm/ directory. Status 1 when there is none
# (a release from before the System Settings pages) or the download failed.
fetch_kcm_source() {
    src=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("tarball_url") or "")' \
        "$1/release.json" 2>/dev/null) && [ -n "$src" ] || return 1
    mkdir -p "$1/src" \
        && curl -fsSL --max-time 120 -o "$1/src.tar.gz" "$src" \
        && tar -xzf "$1/src.tar.gz" -C "$1/src" || return 1
    for d in "$1"/src/*/kcm; do
        [ -f "$d/CMakeLists.txt" ] && { printf '%s\n' "$d"; return 0; }
    done
    return 1
}

# build_kcm SRC: configure, build and install Haus.Licht-plasma's kcm/ per
# user (~/.local/lib/qt6/plugins, ~/.local/share/systemsettings/categories)
build_kcm() {
    build=$(mktemp -d)
    if cmake -S "$1" -B "$build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$HOME/.local" \
            -DKDE_INSTALL_USE_QT_SYS_PATHS=OFF -DKDE_INSTALL_QTPLUGINDIR=lib/qt6/plugins >"$build.log" 2>&1 \
        && cmake --build "$build" -j "$(nproc 2>/dev/null || echo 2)" >>"$build.log" 2>&1 \
        && cmake --install "$build" >>"$build.log" 2>&1; then
        rm -rf "$build" "$build.log"
        return 0
    fi
    echo "Haus.Licht's System Settings pages did not build (log: $build.log)" >&2
    rm -rf "$build"
    return 1
}

# install_widget PATH: a .plasmoid file or a package directory
install_widget() {
    kpackagetool6 --type Plasma/Applet --upgrade "$1" 2>/dev/null \
        || kpackagetool6 --type Plasma/Applet --install "$1"
}

if [ -n "$dry" ]; then
    py=$(command -v python3)
    [ -x "$dir/.venv/bin/python" ] && py="$dir/.venv/bin/python"
    echo "pip install -r $dir/requirements.txt"
    if [ -n "$kinds" ]; then
        reqs=$("$py" "$dir/hauslicht.py" backends --requires "$kinds")
        # shellcheck disable=SC2086
        echo "pip install" $reqs
    fi
    exit 0
fi

[ -x "$dir/.venv/bin/python" ] || python3 -m venv "$dir/.venv"
"$dir/.venv/bin/pip" install -q -r "$dir/requirements.txt"
if [ -n "$kinds" ]; then
    reqs=$("$dir/.venv/bin/python" "$dir/hauslicht.py" backends --requires "$kinds")
    # shellcheck disable=SC2086
    [ -z "$reqs" ] || "$dir/.venv/bin/pip" install -q $reqs
fi

mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
ln -sf "$dir/hauslicht" "$HOME/.local/bin/hauslicht"
ln -sf "$dir/hauslicht-ctl" "$HOME/.local/bin/hauslicht-ctl"
# man pages (man hauslicht, man hauslicht-ctl; German under de), linked like the commands
mkdir -p "$HOME/.local/share/man/man1" "$HOME/.local/share/man/de/man1"
for page in hauslicht.1 hauslicht-ctl.1; do
    ln -sf "$dir/man/$page" "$HOME/.local/share/man/man1/$page"
    ln -sf "$dir/man/de/$page" "$HOME/.local/share/man/de/man1/$page"
done

# replace earlier symlinks instead of writing through them into the checkout
rm -f "$HOME/.config/systemd/user/hauslicht.service"
sed -e "s|^ExecStart=.*|ExecStart=$dir/.venv/bin/python $dir/hauslicht.py sync|" \
    -e "s|^ExecStopPost=.*|ExecStopPost=-$dir/.venv/bin/python $dir/hauslicht.py disarm --if-armed|" \
    "$dir/hauslicht.service" > "$HOME/.config/systemd/user/hauslicht.service"
# The daemon's app id for xdg-desktop-portal (follow video: the screen-cast
# permission is kept under it) and of the settings window (hauslicht config:
# its icon and name; the Settings action opens it); hidden, not a launcher.
# Exec gets the full path: a desktop session's PATH may lack ~/.local/bin.
mkdir -p "$HOME/.local/share/applications"
rm -f "$HOME/.local/share/applications/io.github.gelaende51.HausLicht.desktop"
sed "s|^Exec=hauslicht |Exec=$HOME/.local/bin/hauslicht |" "$dir/install/io.github.gelaende51.HausLicht.desktop" \
    > "$HOME/.local/share/applications/io.github.gelaende51.HausLicht.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$HOME/.local/share/applications" || true
# Plasma tray widget (shows up in the system tray after the next Plasma start).
# Without Plasma (no kpackagetool6) its package is only unpacked, to
# ~/.local/share/hauslicht/plasmoid: hauslicht config shows its Configure pages.
# A failed download leaves the service installed: only the widget is missing.
pages="$HOME/.local/share/hauslicht/plasmoid"
# unpack_pages PATH: a .plasmoid file or a package directory, replacing $pages
unpack_pages() {
    rm -rf "$pages.new" && mkdir -p "$pages.new" || return 1
    if [ -d "$1" ]; then
        cp -R "$1/." "$pages.new/"
    else
        "$dir/.venv/bin/python" -m zipfile -e "$1" "$pages.new"
    fi
    if [ -f "$pages.new/contents/standalone/main.qml" ]; then
        rm -rf "$pages" && mv "$pages.new" "$pages"
    else
        rm -rf "$pages.new"
        return 1
    fi
}
if command -v kpackagetool6 >/dev/null; then
    widget=install_widget
else
    widget=unpack_pages
    echo "no kpackagetool6 (not KDE Plasma 6?): the tray widget's pages go to $pages for hauslicht config"
fi
tmp=$(mktemp -d)
if [ -n "$plasmoid" ]; then
    $widget "$plasmoid" || echo "the tray widget from $plasmoid did not install" >&2
else
    if file=$(fetch_widget "$tmp"); then
        $widget "$file" || echo "the tray widget did not install" >&2
    else
        echo "The tray widget was not installed: no release of $widget_repo could be fetched" >&2
        echo "(offline?). The service is installed. For the widget, run install/install.sh again" >&2
        echo "later, or download the .plasmoid from https://github.com/$widget_repo/releases" >&2
        echo "and run install/install.sh --plasmoid FILE." >&2
    fi
fi

# Plasma 6 System Settings: Haus.Licht's pages as KCMs, System Settings →
# Hardware ("Connected Devices") → Haus.Licht → Bulbs, Profiles, Sound, Video,
# Notifications, from Haus.Licht-plasma's kcm/ (docs/system-settings.md).
# Built here when there is System Settings and the build tools (cmake, a C++
# compiler, extra-cmake-modules, the KF6 KCMUtils headers): per user into
# ~/.local/lib/qt6/plugins, where Qt looks only when QT_PLUGIN_PATH names it,
# which ~/.config/plasma-workspace/env/hauslicht-kcm.sh does from the next
# login. Without them, or when the build fails, the entry under Hardware is
# an external application that runs hauslicht config. Never both: the
# category replaces that entry. Harmless where there is no Plasma.
kcm_user="$HOME/.local/lib/qt6/plugins/plasma/kcms/systemsettings_qwidgets"
kcm_category="$HOME/.local/share/systemsettings/categories/settings-hardware-hauslicht.desktop"
kcm_env="$HOME/.config/plasma-workspace/env/hauslicht-kcm.sh"
external="$HOME/.local/share/plasma/systemsettings/externalmodules/hauslicht-systemsettings.desktop"
qt_plugins=$(qtpaths6 --query QT_INSTALL_PLUGINS 2>/dev/null || qtpaths --query QT_INSTALL_PLUGINS 2>/dev/null) \
    || qt_plugins=/usr/lib/qt6/plugins
if [ -f "$qt_plugins/plasma/kcms/systemsettings_qwidgets/kcm_hauslicht_bulbs.so" ]; then
    # installed with the system (a distribution package): a copy per user
    # would list every page twice
    rm -f "$kcm_user"/kcm_hauslicht_*.so "$kcm_category" "$kcm_env" "$external"
    echo "System Settings: Haus.Licht's pages come with the system ($qt_plugins)"
else
    kcm_src=""
    if [ -n "$plasmoid" ] && [ -d "$plasmoid" ] && [ -f "$plasmoid/../kcm/CMakeLists.txt" ]; then
        kcm_src=$(cd "$plasmoid/../kcm" && pwd)
    elif [ -f "$tmp/release.json" ]; then
        kcm_src=$(fetch_kcm_source "$tmp") || kcm_src=""
    fi
    if [ -n "$kcm_src" ] && command -v systemsettings >/dev/null && command -v cmake >/dev/null \
            && { command -v c++ >/dev/null || command -v g++ >/dev/null || command -v clang++ >/dev/null; }; then
        build_kcm "$kcm_src" || true
    fi
    if [ -f "$kcm_user/kcm_hauslicht_bulbs.so" ] && [ -f "$kcm_category" ]; then
        mkdir -p "$(dirname "$kcm_env")"
        cp "$dir/install/hauslicht-kcm-env.sh" "$kcm_env"
        rm -f "$external"
        case ":${QT_PLUGIN_PATH:-}:" in
            *":$HOME/.local/lib/qt6/plugins:"*) echo "System Settings: Haus.Licht's pages are under Hardware" ;;
            *) echo "System Settings: Haus.Licht's pages show under Hardware from the next login" \
                    "(hauslicht config opens them before that, unless System Settings is already open)" ;;
        esac
    else
        rm -f "$kcm_category" "$kcm_env"
        mkdir -p "$(dirname "$external")"
        sed "s|^Exec=hauslicht |Exec=$HOME/.local/bin/hauslicht |" "$dir/install/hauslicht-systemsettings.desktop" \
            > "$external"
    fi
fi
rm -rf "$tmp"

systemctl --user daemon-reload
echo "installed. Adopt a bulb (hauslicht adopt), then:"
echo "  systemctl --user enable --now hauslicht"
