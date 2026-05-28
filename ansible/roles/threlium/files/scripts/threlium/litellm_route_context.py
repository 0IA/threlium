"""Двойное хранилище e2e-корреляции LiteLLM: ``threading.local`` (TLS) + ``ContextVar``.

TLS используется синхронными путями (``reasoning`` стадия) на FSM-потоках.
ContextVar используется asyncio-путями (LightRAG aquery/ainsert) на RAG event-loop,
где необходимо параллельное исполнение нескольких aquery без глобального lock.

Набор wire-ключей задаётся билдером :func:`threlium.litellm_correlation_headers.build_litellm_correlation_headers`
(From, To, Message-ID, In-Reply-To с конверта; ``X-Threlium-Route`` из корня треда в notmuch;
``X-Threlium-Call-Site``). Дополнительно в том же dict хранятся внутренние слоты счётчиков seq
и один wire-слот при merge — см.
:func:`merge_litellm_call_kwargs_and_log` / :func:`_merge_litellm_extra_route_headers`
в :mod:`threlium.litellm_client`.
"""
from __future__ import annotations

import contextvars
import threading
from contextvars import Token

class _LitellmCorrelationTls(threading.local):
    headers: dict[str, str] | None = None

_tls = _LitellmCorrelationTls()

_correlation_ctxvar: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "threlium_litellm_correlation", default=None
)


# ── TLS (threading.local) API ─────────────────────────────────────────────────


def get_litellm_http_correlation() -> dict[str, str] | None:
    """Текущий dict для merge ``extra_headers`` на этом OS-потоке (в т.ч. внутренние ключи seq при merge)."""
    return _tls.headers


def set_litellm_http_correlation(headers: dict[str, str] | None) -> None:
    """Заменить снимок заголовков на потоке (``None`` — очистить)."""
    _tls.headers = headers


def clear_litellm_http_correlation() -> None:
    """Снять корреляцию с потока (эквивалентно ``set_litellm_http_correlation(None)``)."""
    _tls.headers = None


# ── ContextVar API (asyncio task-local) ───────────────────────────────────────


def get_litellm_correlation_from_ctxvar() -> dict[str, str] | None:
    """Корреляция из текущего asyncio-контекста задачи (наследуется через ``create_task``)."""
    return _correlation_ctxvar.get()


def set_litellm_correlation_ctxvar(headers: dict[str, str] | None) -> Token[dict[str, str] | None]:
    """Выставить корреляцию в ContextVar; вернуть token для reset."""
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
