"""Канал ``isomorph`` — входящий HTTP-мост (Anthropic Messages + OpenAI chat-completions
поверх одного FSM-контура по схеме long-hold + egress-push).

Это первый **входящий** HTTP-сервер проекта (остальные мосты — клиенты-поллеры). Мост
**stateless**: тред продолжается контент-адресуемыми Message-ID (см. ``history.py`` и
docs/THREAD_MODEL §isomorph), без чтения notmuch.

Запуск: ``threlium-bridge@isomorph`` → :func:`run_bridge`.

``run_bridge`` импортируется лениво (PEP 562): тянет starlette/uvicorn только в процессе моста,
не в FSM-стадии ``egress_isomorph`` (которой нужен лишь ``push_types``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .run_bridge import run_bridge

__all__ = ["run_bridge"]


def __getattr__(name: str) -> Any:
    if name == "run_bridge":
        from .run_bridge import run_bridge  # noqa: PLC0415  (ленивый: uvicorn только в процессе моста)

        return run_bridge
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
