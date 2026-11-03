import asyncio, json, os, socket
from pathlib import Path

import pytest

from haus.common import save_state
from haus.daemon import Daemon
from haus.ipc import serve, connect, cleanup, transport
from tests.test_core import mock_state, FakeDesktop, settle


@pytest.mark.parametrize("transport_mode", ["tcp", "unix"])
def test_ipc(tmp_path, monkeypatch, transport_mode):
    monkeypatch.setenv("HAUSLICHT_TRANSPORT", transport_mode)
    sock = tmp_path / "s.sock"
    path = tmp_path / "state.json"
    save_state(path, mock_state(mock_1={}))

    async def main():
        d = Daemon(path, sock, desktop=FakeDesktop())
        await d.start()
        await settle(d)
        server = await serve(d.handle_client, sock)
        async with server:
            # right token / unix: gets answer
            def _ask():
                s = connect(sock, 5)
                s.sendall(json.dumps({"cmd": "get", "arg": None, "which": None}).encode() + b"\n")
                data = b""
                while not data.endswith(b"\n"):
                    data += s.recv(65536)
                s.close()
                return data
            data = await asyncio.to_thread(_ask)
            assert json.loads(data)["results"][0]["bulb"] == "sync"

            # wrong token gets no answer (tcp only)
            if transport() == "tcp":
                from haus.ipc import _port_path
                port, _ = _port_path(sock).read_text().strip().split()
                raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                raw.settimeout(2)
                raw.connect(("127.0.0.1", int(port)))
                raw.sendall(b"auth wrongtoken\n")
                try:
                    chunk = raw.recv(65536)
                except (socket.timeout, OSError):
                    chunk = b""
                raw.close()
                assert chunk == b""

            if transport_mode == "tcp":
                assert sock.with_suffix(".port").exists()
        await d.stop()
        cleanup(sock)
        assert not sock.with_suffix(".port").exists()

    asyncio.run(main())