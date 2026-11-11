# Autostart

> **Ungetestet:** Diese Anleitung wurde nicht auf echtem macOS oder Windows
> verifiziert. Sie ist aus dem Linux-Setup (systemd-User-Unit) abgeleitet und
> kann Anpassungen erfordern.

Unter Linux läuft der Daemon als systemd-User-Unit `lichthaus.service` mit
`ExecStart = <venv python> <repo>/lichthaus.py sync`.

## macOS (LaunchAgent)

Erstelle `~/Library/LaunchAgents/io.github.gelaende51.lichthaus.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>io.github.gelaende51.lichthaus</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/python</string>
        <string>/path/to/lichthaus.py</string>
        <string>sync</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardErrorPath</key>
    <string>/Users/you/Library/Logs/lichthaus.log</string>
</dict>
</plist>
```

Laden mit:

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.github.gelaende51.lichthaus.plist
```

## Windows (Aufgabenplanung)

Aufgabe erstellen, die bei der Anmeldung ausgeführt wird:

```cmd
schtasks /Create /SC ONLOGON /TN Lichthaus /TR "\"<pythonw.exe>\" \"<path>\lichthaus.py\" sync"
```

Alternative: eine Verknüpfung zum gleichen Befehl in `shell:startup` ablegen.
