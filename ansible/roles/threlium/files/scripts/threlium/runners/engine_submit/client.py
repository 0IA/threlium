"""UNIX-клиент wire-протокола engine (один запрос на соединение)."""
from __future__ import annotations

import socket
from pathlib import Path

from threlium.runners.engine.paths import engine_socket_path
from threlium.runners.engine.wire_io import (
    decode_engine_wire_response,
    encode_wire_line,
    read_wire_line,
)
from threlium.enginewire import EngineWireError, EngineWireOk, EngineWireRequest


def submit_to_engine(
    sock_path: Path,
    req: EngineWireRequest,
) -> EngineWireOk | EngineWireError:
    """Отправить запрос и дождаться одной строки ответа."""

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(str(sock_path))
        sock.sendall(encode_wire_line(req))
        with sock.makefile("rb") as rfile:
            line = read_wire_line(rfile)
    return decode_engine_wire_response(line)


def resolve_engine_socket_path(home_raw: str | None) -> Path:
    """``THRELIUM_HOME`` → путь сокета движка."""

    if not home_raw:
        raise ValueError("THRELIUM_HOME is not set")
    return engine_socket_path(Path(home_raw))
