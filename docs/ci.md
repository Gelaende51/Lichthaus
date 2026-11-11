# Continuous integration

Two GitHub Actions workflows in `.github/workflows/`; each can also be
started by hand on any branch: `gh workflow run <file> --ref <branch>`. The
Plasma widget's checks (qmllint, the icon sets, plasmoidviewer in Plasma 6)
and its releases run in its own repository,
[Lichthaus-plasma](https://github.com/Gelaende51/Lichthaus-plasma).

| Workflow | When | Where | What |
|---|---|---|---|
| `tests.yml` | every push and pull request to main | ubuntu-24.04 | pytest on Python 3.12, 3.13, 3.14; man pages |
| `distros.yml` | Mondays, and by hand | Debian stable, Ubuntu 24.04, Fedora, Arch, openSUSE Tumbleweed containers | `install/install.sh --all-backends`, then pytest on the distribution's Python |
| `port.yml` | push to `port-macwin`, and by hand | macos-latest, windows-latest | pytest on the `tests/test_platform_*.py` files and `tests/test_ipc.py` |

## tests.yml

- **pytest**: `requirements.txt`, `requirements-dev.txt` and
  `requirements-clap.txt`, then amqtt without its websockets pin (see
  requirements-dev.txt). `-rs` lists every skip with its reason. The runner has
  no Bluetooth adapter, PipeWire or D-Bus session; the tests fake those parts
  (`LICHTHAUS_BACKENDS=mock`, fake capture and desktop), so nothing is skipped
  there.
- **man pages**: `dev/gen-man.py --check` (generated pages current), then
  `groff -man -ww -k -z` on `man/*.1` and `man/de/*.1`; any output fails.

## port.yml

Portability smoke test for the macOS/Windows port branch: imports and IPC on
macOS and Windows runners. Installs `requirements.txt`, `requirements-dev.txt`
and `requirements-clap.txt` like `tests.yml`, then runs only
the `tests/test_platform_*.py` files and `tests/test_ipc.py`.

## distros.yml

Each container installs what README "Install" asks for (Python 3 with venv and
pip, BlueZ), runs `install/install.sh --all-backends` (a stand-in `systemctl`
takes its closing `daemon-reload`, as containers have no systemd user
instance), checks what it left in `$HOME` (command and man page links, the
user service, the desktop file, `lichthaus --help`), then installs the test
requirements into the same venv and runs pytest. As root in a container, two
`test_localdevices` cases (read-only files) and three `test_kde` cases (no
node) skip.

## The same locally

```sh
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt -r requirements-clap.txt
.venv/bin/pip install --no-deps amqtt==0.12.1
.venv/bin/python -m pytest -q -rs
.venv/bin/python dev/gen-man.py --check
for p in man/*.1 man/de/*.1; do groff -man -ww -k -z "$p"; done
```

A distribution run, in a copy of the checkout inside the container
(install.sh writes `.venv` there and links into the container's `$HOME`):

```sh
podman run --rm -v "$PWD":/src:ro,Z fedora:latest sh -c '
  dnf install -y python3 python3-pip bluez gcc python3-devel libusb1-devel systemd-devel && cp -r /src /w && cd /w &&
  rm -rf .venv lichthaus*.json* &&
  mkdir -p /tmp/stub && printf "#!/bin/sh\n" > /tmp/stub/systemctl && chmod +x /tmp/stub/systemctl &&
  PATH=/tmp/stub:$PATH install/install.sh --all-backends &&
  .venv/bin/pip install -r requirements-dev.txt -r requirements-clap.txt &&
  .venv/bin/pip install --no-deps amqtt==0.12.1 && .venv/bin/python -m pytest -q -rs'
```

The copy drops the host's `.venv` and the state file (`lichthaus.json`,
keys) before anything runs.
