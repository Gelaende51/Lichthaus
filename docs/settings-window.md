# The settings window (`hauslicht config`)

`hauslicht config` opens the Plasma widget's Configure pages (Bulbs, Profiles,
Sound, Video, Notifications) in a window of their own, titled "Haus.Licht
settings" ("Haus.Licht-Einstellungen" in German). It is for desktops without
the widget (GNOME, XFCE, Cinnamon, tiling window managers) and for other
front ends that want a "Settings…" entry; on Plasma, `hauslicht config`
opens System Settings instead when Haus.Licht's pages are installed there
(`--window` still opens the window). The pages are the widget's own files,
not a second copy: a change to a page shows everywhere.

## How it works

- **The pages** live in the widget's package (repository
  [Haus.Licht-plasma](https://github.com/Gelaende51/Haus.Licht-plasma),
  `package/contents/`). `contents/standalone/main.qml` is a small host
  window: the category list on the left and the page on the right, laid out
  like Plasma's own Configure dialog. It reads the list of pages from the
  widget's `config/config.qml`.
- **Qt's `qml` runtime** (part of Qt Declarative) runs that host, with
  `contents/standalone/imports` first on the QML import path. That directory
  holds stand-ins for the two modules that only work inside Plasma:
  - `org.kde.plasma.plasmoid`: `Plasmoid.configuration` with the keys and
    defaults of the widget's `config/main.xml` (a test in Haus.Licht-plasma
    checks they match). The window acts as widget 1: invert scroll, icon
    set, clap mode and Auto scan are widget 1's in the daemon
    (`widget-settings-set`, `widget_settings` in `get`), the same values
    the tray widget shows; nothing is kept in a file of the window's own.
    The window never writes its defaults there: the widget carries its old
    values over the first time it meets a daemon that keeps them.
  - `org.kde.plasma.configuration`: `ConfigModel` and `ConfigCategory`, so
    `config.qml` loads unchanged.
- **Everything else is the real thing:** Kirigami, KCMUtils (the pages are
  `KCM.SimpleKCM`), KIconThemes (the icon chooser) and Plasma5Support (its
  executable engine runs `hauslicht-ctl`) are ordinary QML modules that work
  without a Plasma session. The pages talk to the daemon as in the widget.
- **No Apply button:** the pages change the daemon directly; the few widget
  settings they hold (icon set, scroll direction, clap mode, Auto scan) go
  to the daemon as soon as they change, and a change made in the tray
  widget shows here with the next poll (10 s). With a daemon from before
  these settings they last until the window closes.

## Where the pages are looked for

In this order; the first package that has `contents/standalone/main.qml`
wins (an older widget without it is passed over):

1. `$HAUSLICHT_PLASMOID` (a package directory, e.g. a Haus.Licht-plasma
   checkout's `package/`);
2. `~/.local/share/plasma/plasmoids/haus.licht.tray` (the widget installed
   per user);
3. `/usr/share/plasma/plasmoids/haus.licht.tray` (installed system-wide);
4. `~/.local/share/hauslicht/plasmoid`: where `install/install.sh` unpacks the
   widget's release when there is no `kpackagetool6` (no Plasma).

## What it needs

`hauslicht config` checks for these before it opens the window and, when one
is missing, prints the packages to install:

| Part | Arch | Debian / Ubuntu | Fedora | openSUSE |
| --- | --- | --- | --- | --- |
| qml runtime | qt6-declarative | qml-qt6 | qt6-qtdeclarative-devel | qt6-declarative-tools |
| Qt Quick Controls, Layouts, Dialogs, QtCore | qt6-declarative | qml6-module-qtquick-controls, -layouts, -dialogs, qml6-module-qtcore | qt6-qtdeclarative | qt6-declarative-imports |
| Kirigami | kirigami | qml6-module-org-kde-kirigami | kf6-kirigami | kf6-kirigami-imports |
| KCMUtils | kcmutils | qml6-module-org-kde-kcmutils | kf6-kcmutils | kf6-kcmutils-imports |
| KIconThemes | kiconthemes | qml6-module-org-kde-iconthemes | kf6-kiconthemes | kf6-kiconthemes-imports |
| Plasma5Support | plasma5support | qml6-module-org-kde-plasma-plasma5support, libplasma5support6 | plasma5support | plasma5support6 |

Debian 13 still ships Plasma5Support's executable engine in
`plasma-workspace`. For the Breeze look outside Plasma, also install
qqc2-desktop-style (Debian: qml6-module-org-kde-desktop, elsewhere
kf6-qqc2-desktop-style) and set `QT_QUICK_CONTROLS_STYLE=org.kde.desktop`;
otherwise Qt's default style is used.

## Window icon and name

On Wayland the compositor finds a window's icon and name through its app id,
which Qt makes from the organisation domain (reversed) and the name the
program was started under. The host sets the domain `gelaende51.github.io`,
and `hauslicht config` starts the runtime through a link named `HausLicht`
(`~/.cache/hauslicht/HausLicht`), so the app id is
`io.github.gelaende51.HausLicht`: Haus.Licht's desktop file, with its
`brightness-high` icon. It also sets
`QT_WAYLAND_DISABLED_INTERFACES=xdg_toplevel_icon_manager_v1`, or Qt would
send the qml runtime's own icon, which compositors prefer. On X11 the window
keeps the qml runtime's icon.

## Messages

The window's Qt messages go to `~/.local/state/hauslicht/config-window.log`
(replaced at each start). `hauslicht config` returns at once and leaves the
window running; when the window quits within its first second and a half (a
QML error), it prints the log instead.

## System Settings (Plasma 6)

On Plasma the same pages are KCMs in System Settings → Hardware →
Haus.Licht, with an external-app entry as the fallback: see
[system-settings.md](system-settings.md).
