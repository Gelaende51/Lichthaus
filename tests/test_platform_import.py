import os
import pkgutil
import sys
import subprocess

import haus


def _code(plat):
    return f'''
import sys, os, types
sys.platform = {plat!r}
if {plat!r} == "win32":
    sys.modules["fcntl"] = None
    sys.modules["_overlapped"] = types.ModuleType("_overlapped")
    class _M(types.ModuleType):
        def __getattr__(self, name):
            return 0
    sys.modules["_winapi"] = _M("_winapi")
    sys.modules["_overlapped"] = _M("_overlapped")
    sys.modules["msvcrt"] = types.ModuleType("msvcrt")
    if hasattr(os, "getuid"):
        del os.getuid
    if hasattr(os, "geteuid"):
        del os.geteuid
import socket
if hasattr(socket, "AF_BLUETOOTH"):
    del socket.AF_BLUETOOTH
if {plat!r} == "darwin":
    import types
    sys.modules["_scproxy"] = types.ModuleType("_scproxy")
    sys.modules["_scproxy"]._get_proxy_settings = lambda: ({{}},)
    sys.modules["_scproxy"]._get_proxies = lambda: {{}}
import haus, pkgutil, importlib
mods = []
for m in pkgutil.walk_packages(haus.__path__, "haus."):
    mods.append(m.name)
for m in pkgutil.walk_packages(__import__("haus.backends", fromlist=["x"]).__path__, "haus.backends."):
    mods.append(m.name)
for name in sorted(set(mods)):
    try:
        importlib.import_module(name)
    except ImportError as e:
        msg = str(e)
        if any(p in msg for p in ("numpy", "tflite", "dbus_fast", "ai_edge_litert")):
            continue
        raise
print("OK")
'''


def test_imports_on_darwin_and_win32():
    for plat in ("darwin", "win32"):
        r = subprocess.run([sys.executable, "-c", _code(plat)], capture_output=True, text=True, cwd=os.getcwd())
        assert r.returncode == 0, (plat, r.stdout, r.stderr)
