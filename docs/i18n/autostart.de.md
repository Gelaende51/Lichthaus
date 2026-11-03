# Autostart

> **Ungetestet:** Diese Anleitung wurde nicht auf echtem macOS oder Windows
> verifiziert. Sie ist aus dem Linux-Setup (systemd-User-Unit) abgeleitet und
> kann Anpassungen erfordern.

Unter Linux läuft der Daemon als systemd-User-Unit `hauslicht.service` mit
`ExecStart = <venv python> <repo>/hauslicht.py sync`.

## macOS (LaunchAgent)

Erstelle `~/Library/LaunchAgents/io.github.gelaende51.hauslicht.plist`:

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

Laden mit:

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.github.gelaende51.hauslicht.plist
```

## Windows (Aufgabenplanung)

Aufgabe erstellen, die bei der Anmeldung ausgeführt wird:

```cmd
schtasks /Create /SC ONLOGON /TN Haus.Licht /TR "\"<pythonw.exe>\" \"<path>\hauslicht.py\" sync"
```

Alternative: eine Verknüpfung zum gleichen Befehl in `shell:startup` ablegen.
