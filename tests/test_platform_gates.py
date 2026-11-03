import asyncio

import pytest

from haus import _platform
from haus.backends import mesh
from tests.test_exclusive import Ctx, MeshBackend


def test_mesh_does_not_claim_hci_off_linux(monkeypatch):
    monkeypatch.setattr(_platform, "IS_LINUX", False)
    monkeypatch.setattr(mesh, "IS_LINUX", False)

    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("hci claiming must not run off Linux")

    monkeypatch.setattr(mesh.hci, "claim", boom)
    monkeypatch.setattr(mesh.hci, "list_adapters", boom)

    b = MeshBackend(Ctx())
    class L:  # a minimal link stand-in
        def abort(self): pass
        async def close(self): pass
    b.link = L()
    b.lock = None
    import contextlib
    b.lock = contextlib.nullcontext()

    async def body():
        b._stop_scanner = lambda: _noop()
        b._start_scanner = lambda: _noop()
        await b._enter_exclusive("")
        assert "unavailable" in b.exclusive_note.lower() or "Linux" in b.exclusive_note
        assert called["n"] == 0

    async def _noop():
        return None

    asyncio.run(body())


def test_cli_setup_root_off_linux(monkeypatch):
    monkeypatch.setattr(_platform, "IS_LINUX", False)
    import haus.cli as cli
    monkeypatch.setattr(cli, "IS_LINUX", False)

    import subprocess, os
    def boom(*a, **k):
        raise AssertionError("no subprocess off Linux")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(os, "execvp", boom, raising=False)

    printed = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(a))
    import sys
    monkeypatch.setattr(sys, "argv", ["hauslicht", "setup-root"])
    rc = cli.main()
    assert rc == 0
