# Haus.Licht in System Settings (Plasma 6)

On Plasma 6, Haus.Licht's settings are part of System Settings: the group
Hardware (category id `hardware`, shown as *Connected Devices*) →
**Haus.Licht**, with five pages: **Bulbs**, **Profiles**, **Sound**,
**Video** and **Notifications** (German: Lampen, Profile, Ton, Video,
Benachrichtigungen). They are the widget's own Configure pages, the same
files as in the widget's Configure dialog and in the
[settings window](settings-window.md): a change to a page shows in all
three. There is no Apply button; the pages change the daemon directly.

## How it works

- **Five KCMs** (`kcm_hauslicht_bulbs`, `_profiles`, `_sound`, `_video`,
  `_notifications`), built from Haus.Licht-plasma's `kcm/`, and a category
  file `settings-hardware-hauslicht.desktop` that puts "Haus.Licht" under
  Hardware.
- **QWidget KCMs, not QML KCMs.** They are installed in the plugin namespace
  `plasma/kcms/systemsettings_qwidgets`. Each hosts its page
  (`contents/standalone/kcm.qml` of the installed widget) in a QQuickWidget
  with a QML engine of its own. The pages import `org.kde.plasma.plasmoid`,
  which only works inside plasmashell, so they need the stand-ins in
  `contents/standalone/imports`, as the window does. System Settings runs
  all its QML KCMs and its own window on one shared engine: a stand-in put
  on that engine's import path would answer every `import
  org.kde.plasma.plasmoid` in System Settings. On a private engine nothing
  else sees it.
- Like the window, the pages act as widget 1. The widget's package is looked
  for in the same places as the window's (`$HAUSLICHT_PLASMOID`, the widget
  installed per user or system-wide, `~/.local/share/hauslicht/plasmoid`);
  without it, or with a widget too old to have `contents/standalone/kcm.qml`,
  a page shows a message instead.

## Install

`install/install.sh` builds the KCMs when there is System Settings and the
build tools (cmake, a C++ compiler, extra-cmake-modules, the KF6 KCMUtils
headers). The source is the `kcm/` beside a checkout given with
`--plasmoid DIR`, otherwise the source of the widget's latest release.

- **Per user:** the plugins go to `~/.local/lib/qt6/plugins`, the category
  file to `~/.local/share/systemsettings/categories`. Qt looks in that plugin
  directory only when `QT_PLUGIN_PATH` names it:
  `~/.config/plasma-workspace/env/hauslicht-kcm.sh` (from
  `install/hauslicht-kcm-env.sh`) appends it at login, so the pages show from
  the next login on.
- **System-wide** (e.g. a distribution package, or `cmake --install` with
  prefix `/usr`): when Qt's own plugin directory has the KCMs, that copy wins;
  install.sh removes the per-user plugins, category, env script and
  external-app entry, or every page would be listed twice.
- **Fallback:** without the build tools, or when the build fails, the entry
  under Hardware is an external application instead:
  `install/hauslicht-systemsettings.desktop`, installed in
  `~/.local/share/plasma/systemsettings/externalmodules/`, runs
  `hauslicht config`. Never both: once the KCMs are installed, install.sh
  drops that entry.

## Commands

- `hauslicht config` opens System Settings at Bulbs (`systemsettings
  kcm_hauslicht_bulbs`) on Plasma when the KCMs are installed; otherwise the
  [settings window](settings-window.md). Before the next login it adds
  `~/.local/lib/qt6/plugins` to `QT_PLUGIN_PATH` for that start, which helps
  only while no System Settings is open: an open one would just take the
  arguments over, so then the window opens with a note.
- `hauslicht config --window` opens the standalone window, also on Plasma.
- `hauslicht config --system-settings [--add-bulb]` opens only System
  Settings (`--add-bulb`: the Bulbs page with its Add dialog, also when the
  page is open already) and exits with **3**, opening nothing, where System
  Settings has no Haus.Licht.
- The widget's **Connect new bulb…** runs `hauslicht config --system-settings
  --add-bulb`. When that exits non-zero (no KCMs, not Plasma, no `hauslicht`
  command) the widget opens its own Configure dialog at Bulbs instead.

## Troubleshooting

- **The pages do not show:** `QT_PLUGIN_PATH` is read at login; log out and
  in once after the first install. Until then `hauslicht config` can open
  them while System Settings is closed.
- **No "Haus.Licht" under Hardware:** System Settings hides a category
  without modules, so a category file without plugins it can find shows
  nothing.
- **Is the plugin found?**
  - `echo $QT_PLUGIN_PATH` should contain `~/.local/lib/qt6/plugins`
    (per-user install), and
    `ls ~/.local/lib/qt6/plugins/plasma/kcms/systemsettings_qwidgets/` should
    list `kcm_hauslicht_*.so`.
  - `systemsettings --list | grep hauslicht` lists the five modules when
    System Settings sees them.
  - `kcmshell6 kcm_hauslicht_bulbs` opens one page in a window of its own.
  - The build log is kept when the build fails; install.sh prints its path.
