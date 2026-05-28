"""Предзагруженный движок FSM: сокет + :func:`process_thread_message`."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["process_thread_message"]

if TYPE_CHECKING:
    from threlium.runners.engine.fsm import process_thread_message as process_thread_message


def __getattr__(name: str) -> object:
    if name == "process_thread_message":
        from threlium.runners.engine.fsm import process_thread_message

        return process_thread_message
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
