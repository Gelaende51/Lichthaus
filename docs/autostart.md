# Autostart

> **Untested:** these instructions have not been verified on real macOS or
> Windows. They are derived from the Linux setup (systemd user unit) and may
> need adjustment.

On Linux the daemon runs as a systemd user unit `hauslicht.service` with
`ExecStart = <venv python> <repo>/hauslicht.py sync`.

## macOS (LaunchAgent)

Create `~/Library/LaunchAgents/io.github.gelaende51.hauslicht.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>io.github.gelaende51.hauslicht</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/python</string>
        <string>/path/to/hauslicht.py</string>
        <string>sync</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardErrorPath</key>
    <string>/Users/you/Library/Logs/hauslicht.log</string>
</dict>
</plist>
```

Load it with:

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.github.gelaende51.hauslicht.plist
```

## Windows (Task Scheduler)

Create a task that runs at logon:

```cmd
schtasks /Create /SC ONLOGON /TN Haus.Licht /TR "\"<pythonw.exe>\" \"<path>\hauslicht.py\" sync"
```

Alternative: place a shortcut to the same command in `shell:startup`.
