"""Протокол JSON по одной строке: submit → ``threlium.runners.engine`` (UNIX stream).

Клиент: ``python -m threlium.runners.engine_submit``; кодек — ``threlium.runners.engine.wire_io``.
См. ``docs/ORCHESTRATION.md``, ``docs/TYPES.md`` уровень 1.
"""
from __future__ import annotations

from typing import Literal, Self

import msgspec


class EngineWireRequest(msgspec.Struct, frozen=True):
    """Тело запроса: ``stage`` (local-part стадии), ``thread_id`` (суффикс треда notmuch)."""

    stage: str
    thread_id: str

    @classmethod
    def from_work_instance(cls, instance: str) -> Self:
        """Разбор systemd instance ``%i`` = ``<stage>:<thread_id>``."""

        stage, sep, thread_id = instance.partition(":")
        if not sep or not stage or not thread_id:
            raise ValueError(f"invalid work instance: {instance!r}")
        return cls(stage=stage, thread_id=thread_id)


class EngineWireOk(msgspec.Struct, frozen=True):
    status: Literal["ok"]


class EngineWireError(msgspec.Struct, frozen=True):
    status: Literal["error"]
    message: str
    traceback: str
