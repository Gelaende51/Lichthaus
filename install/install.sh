#!/bin/sh
# Per-user install of Lichthaus from this checkout (no root; for the slider
# also run install/setup-root.sh once with sudo). On Plasma it also builds and
# installs the System Settings pages (KCMs) and their login env script.
#
#   install/install.sh                        mesh bulbs only (Ledvance Volkslicht)
#   install/install.sh --backend wiz,hue      also the libraries these kinds need
#   install/install.sh --all-backends         the libraries of every kind
#   install/install.sh --dry-run ...          print the pip command, change nothing
#   install/install.sh --plasmoid PATH        the tray widget from this .plasmoid file or
#                                             package dir (default: its latest release); the
#                                             package dir of a Lichthaus-plasma checkout
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
        -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option $1 (try --help)" >&2; exit 2 ;;
    esac
done
case ",$kinds," in *,all,*) kinds="all" ;; esac
if [ -n "$plasmoid" ] && [ ! -e "$plasmoid" ]; then
    echo "--plasmoid: $plasmoid does not exist" >&2; exit 2
fi

# The Plasma tray widget lives in its own repository; its releases carry
# lichthaus.tray-<version>.plasmoid and a .sha256 beside it.
widget_repo=Gelaende51/Lichthaus-plasma
widget_api=${LICHTHAUS_WIDGET_API:-https://api.github.com/repos/$widget_repo/releases/latest}

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

# build_kcm SRC: configure, build and install Lichthaus-plasma's kcm/ per
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
    echo "Lichthaus's System Settings pages did not build (log: $build.log)" >&2
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
        reqs=$("$py" "$dir/lichthaus.py" backends --requires "$kinds")
        # shellcheck disable=SC2086
        echo "pip install" $reqs
    fi
    exit 0
fi

# An install from before the rename to Lichthaus: its service stopped and
# removed, its files moved or removed (install/migrate-rename.sh); the panel
# follows once the new widget is there (below)
sh "$dir/install/migrate-rename.sh" before "$dir"

[ -x "$dir/.venv/bin/python" ] || python3 -m venv "$dir/.venv"
"$dir/.venv/bin/pip" install -q -r "$dir/requirements.txt"
if [ -n "$kinds" ]; then
    reqs=$("$dir/.venv/bin/python" "$dir/lichthaus.py" backends --requires "$kinds")
    # shellcheck disable=SC2086
    [ -z "$reqs" ] || "$dir/.venv/bin/pip" install -q $reqs
fi

mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
ln -sf "$dir/lichthaus" "$HOME/.local/bin/lichthaus"
ln -sf "$dir/lichthaus-ctl" "$HOME/.local/bin/lichthaus-ctl"
# man pages (man lichthaus, man lichthaus-ctl; German under de), linked like the commands
mkdir -p "$HOME/.local/share/man/man1" "$HOME/.local/share/man/de/man1"
for page in lichthaus.1 lichthaus-ctl.1; do
    ln -sf "$dir/man/$page" "$HOME/.local/share/man/man1/$page"
    ln -sf "$dir/man/de/$page" "$HOME/.local/share/man/de/man1/$page"
done

# replace earlier symlinks instead of writing through them into the checkout
rm -f "$HOME/.config/systemd/user/lichthaus.service"
sed -e "s|^ExecStart=.*|ExecStart=$dir/.venv/bin/python $dir/lichthaus.py sync|" \
    -e "s|^ExecStopPost=.*|ExecStopPost=-$dir/.venv/bin/python $dir/lichthaus.py disarm --if-armed|" \
    "$dir/lichthaus.service" > "$HOME/.config/systemd/user/lichthaus.service"
# The daemon's app id for xdg-desktop-portal (follow video: the screen-cast
# permission is kept under it) and of the settings window (lichthaus config:
# its icon and name; the Settings action opens it); hidden, not a launcher.
# Exec gets the full path: a desktop session's PATH may lack ~/.local/bin.
mkdir -p "$HOME/.local/share/applications"
rm -f "$HOME/.local/share/applications/io.github.gelaende51.Lichthaus.desktop"
sed "s|^Exec=lichthaus |Exec=$HOME/.local/bin/lichthaus |" "$dir/install/io.github.gelaende51.Lichthaus.desktop" \
    > "$HOME/.local/share/applications/io.github.gelaende51.Lichthaus.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$HOME/.local/share/applications" || true
# Plasma tray widget (shows up in the system tray after the next Plasma start).
# Without Plasma (no kpackagetool6) its package is only unpacked, to
# ~/.local/share/lichthaus/plasmoid: lichthaus config shows its Configure pages.
# A failed download leaves the service installed: only the widget is missing.
pages="$HOME/.local/share/lichthaus/plasmoid"
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
    echo "no kpackagetool6 (not KDE Plasma 6?): the tray widget's pages go to $pages for lichthaus config"
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
# panel widgets of the old id get the new one (install/migrate-rename.sh)
sh "$dir/install/migrate-rename.sh" panel

# Plasma 6 System Settings: Lichthaus's pages as KCMs, System Settings →
# Connected Devices (category `hardware`) → Lichthaus → Bulbs, Profiles, Sound, Video,
# Notifications, from Lichthaus-plasma's kcm/ (docs/system-settings.md).
# Built here when there is System Settings and the build tools (cmake, a C++
# compiler, extra-cmake-modules, the KF6 KCMUtils headers): per user into
# ~/.local/lib/qt6/plugins, where Qt looks only when QT_PLUGIN_PATH names it,
# which ~/.config/plasma-workspace/env/lichthaus-kcm.sh does from the next
# login. Without them, or when the build fails, the entry under Connected Devices is
# an external application that runs lichthaus config. Never both: the
# category replaces that entry. Harmless where there is no Plasma.
kcm_user="$HOME/.local/lib/qt6/plugins/plasma/kcms/systemsettings_qwidgets"
kcm_category="$HOME/.local/share/systemsettings/categories/settings-hardware-lichthaus.desktop"
kcm_env="$HOME/.config/plasma-workspace/env/lichthaus-kcm.sh"
external="$HOME/.local/share/plasma/systemsettings/externalmodules/lichthaus-systemsettings.desktop"
qt_plugins=$(qtpaths6 --query QT_INSTALL_PLUGINS 2>/dev/null || qtpaths --query QT_INSTALL_PLUGINS 2>/dev/null) \
    || qt_plugins=/usr/lib/qt6/plugins
if [ -f "$qt_plugins/plasma/kcms/systemsettings_qwidgets/kcm_lichthaus_bulbs.so" ]; then
    # installed with the system (a distribution package): a copy per user
    # would list every page twice
    rm -f "$kcm_user"/kcm_lichthaus_*.so "$kcm_category" "$kcm_env" "$external"
    echo "System Settings: Lichthaus's pages come with the system ($qt_plugins)"
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
    if [ -f "$kcm_user/kcm_lichthaus_bulbs.so" ] && [ -f "$kcm_category" ]; then
        mkdir -p "$(dirname "$kcm_env")"
        cp "$dir/install/lichthaus-kcm-env.sh" "$kcm_env"
        rm -f "$external"
        case ":${QT_PLUGIN_PATH:-}:" in
            *":$HOME/.local/lib/qt6/plugins:"*) echo "System Settings: Lichthaus's pages are under Connected Devices" ;;
            *) echo "System Settings: Lichthaus's pages show under Connected Devices from the next login" \
                    "(lichthaus config opens them before that, unless System Settings is already open)" ;;
        esac
    else
        rm -f "$kcm_category" "$kcm_env"
        mkdir -p "$(dirname "$external")"
        sed "s|^Exec=lichthaus |Exec=$HOME/.local/bin/lichthaus |" "$dir/install/lichthaus-systemsettings.desktop" \
            > "$external"
    fi
fi
rm -rf "$tmp"

systemctl --user daemon-reload
echo "installed. Adopt a bulb (lichthaus adopt), then:"
echo "  systemctl --user enable --now lichthaus"
