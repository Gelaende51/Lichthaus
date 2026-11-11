# Rename: Haus.Licht -> Lichthaus (2026-11-11)

The user: "renamed again to Lichthaus. no CamelCase, no dot", for the whole
project, technical names included, with a migration for existing installs.

## Name map (every repo: Haus.Licht, -plasma, -gnome, -cinnamon, -xfce, -tray, -bar)

| Old | New | Where |
|---|---|---|
| `Haus.Licht` | `Lichthaus` | display name, README, docs, UI strings (EN and DE), repo names |
| `HausLicht` | `Lichthaus` | ids such as `io.github.gelaende51.HausLicht`, `X-HausLicht-Page` |
| `hauslicht` | `lichthaus` | commands (`hauslicht`, `hauslicht-ctl`, `hauslicht.py`), service, state file `hauslicht.json`, `~/.config/hauslicht`, `/usr/local/lib/hauslicht`, man pages, socket, `kcm_hauslicht_*`, settings category, udev/polkit file names, GNOME/Cinnamon/XFCE ids and UUIDs |
| `HAUSLICHT` | `LICHTHAUS` | environment variables (`HAUSLICHT_SOCK`, `HAUSLICHT_DEBUG`, ...) |
| `haus.licht` | `lichthaus` | the plasmoid id `haus.licht.tray` -> `lichthaus.tray`, its release asset `lichthaus.tray-<v>.plasmoid` |
| `Haus.Licht-plasma` etc. | `Lichthaus-plasma` etc. | repo names, URLs (`github.com/Gelaende51/Lichthaus-plasma`) |

Kept on purpose:
- `Licht.Haus` (the upstream this was forked from, `github.com/Gelaende51/Licht.Haus`): attribution stays as is.
- The Python package `haus/` and code identifiers like `HausCtl`: internal, not the project's name.
- Past commit messages, and dated log or measurement quotes in `dev/` notes, may keep the old name where they quote history.

The mechanical part is `dev/rename-lichthaus.py` (content and paths, with the exceptions above), so every repo and branch (main, port-macwin) gets the identical rename and merges stay clean.

## Migration (an existing install, such as this machine)

This machine also has leftovers of the old Licht.Haus checkout under the new names: `lichthaus.service` (disabled, ExecStart into `~/building_stuff/Licht.Haus`, drop-in `lichthaus.service.d/debug.conf`), `~/.local/bin/lichthaus{,-ctl}` -> Licht.Haus, `/usr/local/lib/lichthaus/conn-update`. The migration replaces them.

`install/install.sh`:
- Stops and disables `hauslicht.service`, removes its unit file; a `hauslicht.service.d/` drop-in moves to `lichthaus.service.d/` with `HAUSLICHT_` -> `LICHTHAUS_` (an existing `lichthaus.service.d/` from Licht.Haus is set aside as `.old-licht-haus`, said so).
- Moves `~/.config/hauslicht` -> `~/.config/lichthaus` when the new one is missing.
- Removes the old links in `~/.local/bin`, the old man pages, desktop file, plasmoid `haus.licht.tray`, KCMs and their category file, then installs the new ones (links replaced with `ln -sf`).
- Plasma panel: an applet with `plugin=haus.licht.tray` in `~/.config/plasma-org.kde.plasma.desktop-appletsrc` becomes `plugin=lichthaus.tray` (same applet id, position and config). The file is backed up first, and edited only while plasmashell is stopped (stop, sed, start); without a systemd-run plasmashell, a note says what to do instead.

The daemon at start:
- State file `hauslicht.json` (and its backups `hauslicht.json.*`) next to the code -> `lichthaus.json` by `os.replace` when the new one is missing. Never copy (the mesh SEQ counter must not exist twice), never read or print its content in logs.
- Root helpers: `/usr/local/lib/lichthaus` when its `setup-version` is there, else `/usr/local/lib/hauslicht` until `setup-root` ran again (the old Licht.Haus left an outdated `/usr/local/lib/lichthaus/conn-update` without `setup-version`).

`install/setup-root.sh` (run by the user, needs the password): installs to `/usr/local/lib/lichthaus` and the new rule/policy names, removes `/usr/local/lib/hauslicht` and the old rule/policy files.

## GitHub (after review, by the orchestrator)
`gh repo rename` Haus.Licht -> Lichthaus, Haus.Licht-plasma -> Lichthaus-plasma (GitHub redirects the old URLs); the others are created under the new names. A plasma release v0.3 carries `lichthaus.tray-0.3.plasmoid`; `install.sh` fetches from `Gelaende51/Lichthaus-plasma`.
