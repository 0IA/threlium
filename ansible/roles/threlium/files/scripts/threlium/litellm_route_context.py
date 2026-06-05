"""Единое хранилище e2e-корреляции LiteLLM: один ``ContextVar``.

ContextVar — носитель и для asyncio-путей (LightRAG aquery/ainsert на RAG event-loop, где нужно
параллельное исполнение нескольких aquery без глобального lock; дочерние задачи наследуют контекст
через ``create_task``), и для синхронных FSM-стадий (``reasoning``/``enrich`` на FSM-потоке): в обычном
потоке ContextVar ведёт себя как thread-local (set→read на одном потоке), а ``fsm._run_stage`` скоупит
корреляцию на сообщение через ``set``→``reset(token)`` (важно, т.к. поток воркера переиспользуется).

Набор wire-ключей задаётся билдером :func:`threlium.litellm_correlation_headers.build_litellm_correlation_headers`
(From, To, Message-ID, In-Reply-To с конверта; ``X-Threlium-Route`` из корня треда в notmuch;
``X-Threlium-Call-Site``). Дополнительно в том же dict хранятся внутренние слоты счётчиков seq
и один wire-слот при merge — см.
:func:`merge_litellm_call_kwargs_and_log` / :func:`_merge_litellm_extra_route_headers`
в :mod:`threlium.litellm_client`.
"""
from __future__ import annotations

import contextvars
from contextvars import Token

_correlation_ctxvar: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "threlium_litellm_correlation", default=None
)


def get_litellm_correlation_from_ctxvar() -> dict[str, str] | None:
    """Корреляция из текущего контекста (asyncio-задача наследует через ``create_task``; на синхронном
    FSM-потоке — значение, выставленное на этом же потоке)."""
    return _correlation_ctxvar.get()


def set_litellm_correlation_ctxvar(headers: dict[str, str] | None) -> Token[dict[str, str] | None]:
    """Выставить корреляцию в ContextVar; вернуть token для reset (per-message-скоуп)."""
    return _correlation_ctxvar.set(headers)


def reset_litellm_correlation_ctxvar(token: Token[dict[str, str] | None]) -> None:
    """Сбросить ContextVar к предыдущему значению."""
    _correlation_ctxvar.reset(token)


def e2e_route_wire_tail(wire: str | None, *, tail_n: int = 32) -> str:
    """Короткий хвост b62-wire ``X-Threlium-Route`` для e2e-логов (без проверки флага корреляции)."""

    if not wire:
        return "?"
    s = str(wire).strip()
    if not s:
        return "?"
    if len(s) <= tail_n:
        return s
    return f"...{s[-tail_n:]}"
