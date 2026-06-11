"""JSON по одной строке: клиент submit и сервер ``threlium.runners.engine``."""
from __future__ import annotations

from typing import IO

import msgspec

from threlium.enginewire import EngineWireError, EngineWireOk, EngineWireRequest
from threlium.logutil import logger


def encode_wire_line(obj: msgspec.Struct) -> bytes:
    """Сериализовать Struct в одну JSON-строку с переводом строки."""

    return msgspec.json.encode(obj) + b"\n"


def read_wire_line(rfile: IO[bytes]) -> bytes:
    """Прочитать одну wire-строку (до ``\\n`` или EOF), без strip полезной нагрузки."""

    raw = rfile.readline()
    if not raw:
        return b""
    return raw.strip()


def decode_engine_wire_request(line: bytes) -> EngineWireRequest:
    """Десериализовать запрос submit → engine."""

    if not line:
        raise ValueError("empty engine request line")
    try:
        return msgspec.json.decode(line, type=EngineWireRequest)
    except (msgspec.DecodeError, msgspec.ValidationError) as e:
        raise ValueError(f"Invalid engine request JSON: {e}") from e


def decode_engine_wire_response(line: bytes) -> EngineWireOk | EngineWireError:
    """Десериализовать ответ engine → submit."""

    if not line:
        raise ValueError("empty engine response line")
    try:
        return msgspec.json.decode(line, type=EngineWireOk)
    except (msgspec.DecodeError, msgspec.ValidationError) as exc:
        logger.debug("engine_response_not_ok_trying_error", exc_info=exc)
    try:
        return msgspec.json.decode(line, type=EngineWireError)
    except (msgspec.DecodeError, msgspec.ValidationError) as e:
        raise ValueError(f"Invalid engine response JSON: {e}") from e
