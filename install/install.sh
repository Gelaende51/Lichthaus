#!/bin/sh
# Per-user install of Haus.Licht from this checkout (no root; for the slider
# also run install/setup-root.sh once with sudo).
#
#   install/install.sh                        mesh bulbs only (Ledvance Volkslicht)
#   install/install.sh --backend wiz,hue      also the libraries these kinds need
#   install/install.sh --all-backends         the libraries of every kind
#   install/install.sh --dry-run ...          print the pip command, change nothing
#   install/install.sh --plasmoid PATH        the tray widget from this .plasmoid file or
#                                             package dir (default: its latest release)
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
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
# DIR, check the sum, print the .plasmoid's path. On failure: the reason on
# stderr, status 1.
fetch_widget() {
    json=$(curl -fsSL --max-time 30 -H "Accept: application/vnd.github+json" "$widget_api" 2>&1) \
        || { echo "the GitHub API did not answer ($json)" >&2; return 1; }
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
# permission is kept under it); hidden, not a launcher
mkdir -p "$HOME/.local/share/applications"
cp "$dir/install/io.github.gelaende51.HausLicht.desktop" "$HOME/.local/share/applications/"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$HOME/.local/share/applications" || true
# Plasma tray widget (shows up in the system tray after the next Plasma start).
# A failed download leaves the service installed: only the widget is missing.
if ! command -v kpackagetool6 >/dev/null; then
    echo "no kpackagetool6 (not KDE Plasma 6?): the tray widget is not installed"
elif [ -n "$plasmoid" ]; then
    install_widget "$plasmoid" || echo "the tray widget from $plasmoid did not install" >&2
else
    tmp=$(mktemp -d)
    if file=$(fetch_widget "$tmp"); then
        install_widget "$file" || echo "the tray widget did not install" >&2
    else
        echo "The tray widget was not installed: no release of $widget_repo could be fetched" >&2
        echo "(offline?). The service is installed. For the widget, run install/install.sh again" >&2
        echo "later, or download the .plasmoid from https://github.com/$widget_repo/releases" >&2
        echo "and run install/install.sh --plasmoid FILE." >&2
    fi
    rm -rf "$tmp"
fi

systemctl --user daemon-reload
echo "installed. Adopt a bulb (hauslicht adopt), then:"
echo "  systemctl --user enable --now hauslicht"
