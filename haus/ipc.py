"""The ctl transport: a Unix socket, or on Windows (and with LICHTHAUS_TRANSPORT=tcp)
TCP on 127.0.0.1 with a token in a port file beside the socket path."""
from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import socket
from pathlib import Path

from haus._platform import IS_WIN


def transport() -> str:
    if IS_WIN:
        return "tcp"
    if os.environ.get("LICHTHAUS_TRANSPORT") == "tcp":
        return "tcp"
    return "unix"


def _port_path(sock_path) -> Path:
    return Path(sock_path).with_suffix(".port")


async def serve(handler, sock_path):
    sock_path = Path(sock_path)
    if transport() == "unix":
        with contextlib.suppress(FileNotFoundError):
            sock_path.unlink()
        server = await asyncio.start_unix_server(handler, path=str(sock_path))
        try:
            os.chmod(sock_path, 0o600)
        except (OSError, NotImplementedError):
            pass
        return server
    token = secrets.token_hex(16)

    async def wrapped(reader, writer):
        try:
            line = await reader.readline()
        except Exception:
            writer.close()
            return
        if not secrets.compare_digest(line.strip(), f"auth {token}".encode()):
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return
        await handler(reader, writer)

    server = await asyncio.start_server(wrapped, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    ppath = _port_path(sock_path)
    with contextlib.suppress(FileNotFoundError):
        ppath.unlink()
    fd = os.open(ppath, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)  # the token: owner only
    with os.fdopen(fd, "w") as f:
        f.write(f"{port} {token}\n")
    server._lichthaus_port_path = ppath  # type: ignore[attr-defined]
    return server


def connect(sock_path, timeout=10):
    sock_path = Path(sock_path)
    if transport() == "unix":
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(str(sock_path))
        return s
    ppath = _port_path(sock_path)
    text = ppath.read_text().strip()
    port_str, token = text.split()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(("127.0.0.1", int(port_str)))
    s.sendall(f"auth {token}\n".encode())
    return s


def cleanup(sock_path):
    sock_path = Path(sock_path)
    if transport() == "tcp":
        with contextlib.suppress(FileNotFoundError):
            _port_path(sock_path).unlink()
    else:
        with contextlib.suppress(FileNotFoundError):
            sock_path.unlink()
