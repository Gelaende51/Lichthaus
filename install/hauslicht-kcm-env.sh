# Haus.Licht: Plasma sources this at login (install/install.sh puts it in
# ~/.config/plasma-workspace/env/hauslicht-kcm.sh). Haus.Licht's pages in
# System Settings are KCMs built per user into ~/.local/lib/qt6/plugins,
# where Qt looks only when QT_PLUGIN_PATH names it; set here, it reaches
# System Settings and KRunner (search) from the next login on.
case ":${QT_PLUGIN_PATH:-}:" in
    *":$HOME/.local/lib/qt6/plugins:"*) ;;
    *) export QT_PLUGIN_PATH="${QT_PLUGIN_PATH:+$QT_PLUGIN_PATH:}$HOME/.local/lib/qt6/plugins" ;;
esac
