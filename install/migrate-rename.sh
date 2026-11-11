#!/bin/sh
# rename-lichthaus: keep-file (this script is about the old names)
# Moves a per-user install from before the rename Haus.Licht -> Lichthaus
# (dev/rename-lichthaus.md) to the new names; install/install.sh runs it:
#
#   install/migrate-rename.sh before DIR   before the new names are installed
#                                          (DIR: the checkout)
#   install/migrate-rename.sh panel        after the new tray widget is installed
#
# Each step acts only on what is still there under an old name, so a second
# run changes nothing. The state file (hauslicht.json) is the daemon's to
# move (haus/common.py), the system files setup-root.sh's.
set -eu

units="$HOME/.config/systemd/user"
appletsrc="$HOME/.config/plasma-org.kde.plasma.desktop-appletsrc"

# aside PATH: PATH, or PATH.<time> when PATH is taken (a backup never overwrites one)
aside() {
    if [ -e "$1" ] || [ -L "$1" ]; then
        printf '%s.%s\n' "$1" "$(date +%Y%m%d-%H%M%S)"
    else
        printf '%s\n' "$1"
    fi
}

before() {
    dir=$1
    # While the old unit or its drop-in is there, a lichthaus.service(.d) can
    # only be the older Licht.Haus's (Haus.Licht never used these names): it
    # is stopped, install.sh replaces the unit, and its drop-in, which must not
    # apply to this one, is set aside. Once migrated, nothing here acts again.
    migrating=""
    if [ -e "$units/hauslicht.service" ] || [ -L "$units/hauslicht.service" ] || [ -d "$units/hauslicht.service.d" ]; then
        migrating=1
    fi
    if [ -n "$migrating" ] && [ -f "$units/lichthaus.service" ] \
            && ! grep -qF " $dir/lichthaus.py sync" "$units/lichthaus.service"; then
        systemctl --user stop lichthaus.service 2>/dev/null || true
        systemctl --user disable lichthaus.service 2>/dev/null || true
    fi
    if [ -n "$migrating" ] && [ -d "$units/lichthaus.service.d" ]; then
        old=$(aside "$units/lichthaus.service.d.old-licht-haus")
        mv "$units/lichthaus.service.d" "$old"
        echo "the drop-in lichthaus.service.d of the older Licht.Haus is set aside as ${old##*/}"
    fi

    # The service: stopped, disabled and removed; install.sh writes lichthaus.service.
    if [ -e "$units/hauslicht.service" ] || [ -L "$units/hauslicht.service" ]; then
        enabled=""
        if systemctl --user is-enabled --quiet hauslicht.service 2>/dev/null; then
            enabled=1
        fi
        systemctl --user stop hauslicht.service 2>/dev/null || true
        systemctl --user disable hauslicht.service 2>/dev/null || true
        rm -f "$units/hauslicht.service"
        echo "hauslicht.service stopped, disabled and removed (now lichthaus.service)"
        if [ -n "$enabled" ]; then
            echo "  it was enabled: enable the new one afterwards (systemctl --user enable --now lichthaus)"
        fi
    fi
    for link in "$units"/*.wants/hauslicht.service; do
        # what disable leaves where no systemd user instance runs
        if [ -L "$link" ]; then
            rm -f "$link"
        fi
    done

    if [ -d "$units/hauslicht.service.d" ]; then
        mv "$units/hauslicht.service.d" "$units/lichthaus.service.d"
        for conf in "$units/lichthaus.service.d"/*.conf; do
            if [ -f "$conf" ]; then
                sed 's/HAUSLICHT_/LICHTHAUS_/g' "$conf" > "$conf.new"
                cat "$conf.new" > "$conf"
                rm -f "$conf.new"
            fi
        done
        echo "drop-in hauslicht.service.d moved to lichthaus.service.d (HAUSLICHT_ variables now LICHTHAUS_)"
    fi

    # Directories with the user's data: moved while the new one is missing.
    for base in "$HOME/.config" "${XDG_CACHE_HOME:-$HOME/.cache}"; do
        if [ -d "$base/hauslicht" ] && [ ! -e "$base/lichthaus" ] && [ ! -L "$base/lichthaus" ]; then
            mv "$base/hauslicht" "$base/lichthaus"
            echo "$base/hauslicht moved to $base/lichthaus"
        fi
    done

    # What install.sh put in place under the old names; it installs the new ones.
    removed=""
    for f in "$HOME/.local/bin/hauslicht" "$HOME/.local/bin/hauslicht-ctl" \
            "$HOME/.local/share/man/man1/hauslicht.1" "$HOME/.local/share/man/man1/hauslicht-ctl.1" \
            "$HOME/.local/share/man/de/man1/hauslicht.1" "$HOME/.local/share/man/de/man1/hauslicht-ctl.1"; do
        if [ -L "$f" ]; then  # links only: never a file someone put there
            rm -f "$f"
            removed="$removed ${f##*/}"
        fi
    done
    for f in "$HOME/.local/share/applications/io.github.gelaende51.HausLicht.desktop" \
            "$HOME/.local/share/systemsettings/categories/settings-hardware-hauslicht.desktop" \
            "$HOME/.config/plasma-workspace/env/hauslicht-kcm.sh" \
            "$HOME/.local/share/plasma/systemsettings/externalmodules/hauslicht-systemsettings.desktop" \
            "$HOME/.local/lib/qt6/plugins/plasma/kcms/systemsettings_qwidgets"/kcm_hauslicht_*.so; do
        if [ -f "$f" ]; then
            rm -f "$f"
            removed="$removed ${f##*/}"
        fi
    done
    pages="$HOME/.local/share/hauslicht/plasmoid"  # the widget's pages where there is no Plasma
    if [ -d "$pages" ]; then
        rm -r "$pages"
        rmdir "$HOME/.local/share/hauslicht" 2>/dev/null || true
        removed="$removed ~/.local/share/hauslicht/plasmoid"
    fi
    if [ -d "$HOME/.local/share/plasma/plasmoids/haus.licht.tray" ]; then
        if command -v kpackagetool6 >/dev/null && kpackagetool6 --type Plasma/Applet --remove haus.licht.tray >/dev/null 2>&1; then
            :
        else
            rm -r "$HOME/.local/share/plasma/plasmoids/haus.licht.tray"
        fi
        removed="$removed haus.licht.tray"
    fi
    if [ -n "$removed" ]; then
        echo "removed under the old name Haus.Licht:$removed"
    fi
}

# The panel's widgets are applets with plugin=haus.licht.tray (and the system
# tray lists the id in its item lists): the same applets, positions and
# settings get the new id. plasmashell writes this file when it quits, so it
# is changed only while plasmashell is stopped.
panel() {
    pattern='^plugin=haus\.licht\.tray$|^(extraItems|knownItems|hiddenItems|shownItems)=(.*,)?haus\.licht\.tray(,|$)'
    if [ ! -f "$appletsrc" ] || ! grep -Eq "$pattern" "$appletsrc"; then
        return 0
    fi
    if systemctl --user is-active --quiet plasma-plasmashell.service 2>/dev/null; then
        if ! systemctl --user stop plasma-plasmashell.service; then
            panel_note "plasmashell did not stop"
            return 0
        fi
        rewrite_panel
        systemctl --user start plasma-plasmashell.service \
            || echo "plasmashell did not start again: systemctl --user start plasma-plasmashell" >&2
    elif command -v pgrep >/dev/null && ! pgrep -x plasmashell >/dev/null 2>&1; then
        rewrite_panel  # no plasmashell running: nothing writes the file meanwhile
    else
        panel_note "plasmashell does not run as a systemd service here"
    fi
}

rewrite_panel() {
    backup=$(aside "$appletsrc.hauslicht-bak")
    cp -p "$appletsrc" "$backup"
    sed -E -e 's/^plugin=haus\.licht\.tray$/plugin=lichthaus.tray/' \
        -e '/^(extraItems|knownItems|hiddenItems|shownItems)=/s/([=,])haus\.licht\.tray(,|$)/\1lichthaus.tray\2/g' \
        "$backup" > "$appletsrc.lichthaus-new"
    cat "$appletsrc.lichthaus-new" > "$appletsrc"
    rm -f "$appletsrc.lichthaus-new"
    echo "Plasma panel: the tray widgets now use lichthaus.tray (backup: ${backup##*/})"
}

panel_note() {
    echo "Plasma panel: the tray widget's id is now lichthaus.tray (was haus.licht.tray); $1," >&2
    echo "so the panel was left as it is. Either remove the old widget from the panel and add" >&2
    echo "Lichthaus again, or quit plasmashell (kquitapp6 plasmashell), run install/install.sh" >&2
    echo "again and start plasmashell (kstart plasmashell)." >&2
}

case "${1:-}" in
    before) [ $# -eq 2 ] || { echo "usage: $0 before DIR" >&2; exit 2; }
        before "$2" ;;
    panel) panel ;;
    *) echo "usage: $0 before DIR | panel" >&2; exit 2 ;;
esac
