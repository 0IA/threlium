"""RAG loop lifecycle: thread management, coroutine dispatch, drain scheduling."""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

from lightrag import LightRAG

from threlium.litellm_route_context import (
    reset_litellm_correlation_ctxvar,
    set_litellm_correlation_ctxvar,
)
from threlium.logutil import logger
from threlium.settings import ThreliumSettings, resolve_llm_endpoint
from threlium.systemd_notify import notify_status
from threlium.types import LitellmRoutingSite
from threlium.types.systemd_status import SystemdStatusBody

from threlium.runners.lightrag._construction import build_rag, install_e2e_correlation_bridge
from threlium.runners.lightrag._drain import schedule_on_loop, reset_drain_task

log = logger.bind(stage="lightrag")

_T = TypeVar("_T")

_rag_loop: asyncio.AbstractEventLoop | None = None
_rag_thread: threading.Thread | None = None
_daemon_rag: LightRAG | None = None
_drain_lock: asyncio.Lock | None = None
_start_lock = threading.Lock()
_ready_event = threading.Event()
_boot_error: list[BaseException] = []


def _future_timeout_sec(settings: ThreliumSettings) -> float | None:
    llm_ep = resolve_llm_endpoint(settings.litellm, LitellmRoutingSite.LIGHTRAG_LLM)
    v = float(llm_ep.timeout)
    return v if v > 0 else None


def _rag_loop_shutdown_timeout_sec(settings: ThreliumSettings | None) -> float:
    if settings is None:
        return 30.0
    return float(settings.lightrag.rag_loop_shutdown_timeout_sec)


async def _shutdown_rag_loop() -> None:
    """Отменить все задачи loop (кроме текущей), затем flush storages."""
    me = asyncio.current_task()
    work = [t for t in asyncio.all_tasks() if t is not me and not t.done()]
    for t in work:
        t.cancel()
    if work:
        await asyncio.gather(*work, return_exceptions=True)
        log.info("rag_shutdown_cancelled_tasks", count=len(work))
    if _daemon_rag is not None:
        await _daemon_rag.finalize_storages()


def daemon_lightrag() -> LightRAG | None:
    """Инстанс на RAG-loop (после успешного ``start_rag_loop_thread``)."""
    return _daemon_rag


def run_rag_coroutine(
    coro: Coroutine[Any, Any, _T],
    *,
    settings: ThreliumSettings,
    correlation: dict[str, str] | None = None,
) -> _T:
    """Выполнить корутину LightRAG на выделенном loop (из любого потока движка).

    При ``e2e_litellm_route_correlation`` устанавливает ContextVar на задаче RAG-loop.
    """
    if _rag_loop is None:
        raise RuntimeError("lightrag: RAG event loop is not running (start_rag_loop_thread first)")
    timeout = _future_timeout_sec(settings)

    if settings.e2e.litellm_route_correlation and correlation is not None:
        async def _with_ctxvar() -> _T:
            token = set_litellm_correlation_ctxvar(correlation)
            try:
                return await coro
            finally:
                reset_litellm_correlation_ctxvar(token)

        fut = asyncio.run_coroutine_threadsafe(_with_ctxvar(), _rag_loop)
    else:
        fut = asyncio.run_coroutine_threadsafe(coro, _rag_loop)
    return fut.result(timeout=timeout)


def schedule_index_pending(settings: ThreliumSettings) -> None:
    """Запланировать drain pending на RAG-loop (после ``nm_settle``) без ожидания.

    Паттерн sweep: если задача уже запущена — noop. Иначе создать задачу.
    Цепочка продолжается внутри ``drain_single_batch`` (OnSuccess → self-schedule).
    """
    if _rag_loop is None or _daemon_rag is None or _drain_lock is None:
        log.warning("schedule_index_pending_not_ready")
        return
    rag = _daemon_rag
    lock = _drain_lock
    _rag_loop.call_soon_threadsafe(schedule_on_loop, rag, settings, lock)


def _rag_thread_main(settings: ThreliumSettings) -> None:
    global _rag_loop, _daemon_rag, _drain_lock
    notify_status(SystemdStatusBody.lightrag_thread_starting())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _rag_loop = loop
    _drain_lock = asyncio.Lock()
    _boot_error.clear()
    try:

        async def _boot() -> LightRAG:
            notify_status(SystemdStatusBody.lightrag_initializing_storages())
            rag = build_rag(settings)
            await rag.initialize_storages()
            if settings.e2e.litellm_route_correlation:
                install_e2e_correlation_bridge(rag)
            notify_status(SystemdStatusBody.lightrag_storages_ready())
            return rag

        rag = loop.run_until_complete(_boot())
        _daemon_rag = rag
        _ready_event.set()
        loop.run_forever()
    except BaseException as e:
        notify_status(SystemdStatusBody.lightrag_boot_failed(message=str(e)))
        _boot_error.append(e)
        _ready_event.set()
    finally:
        try:
            if not loop.is_closed():
                loop.close()
        except Exception:
            pass
        _rag_loop = None
        _drain_lock = None
        reset_drain_task()


def start_rag_loop_thread(settings: ThreliumSettings) -> None:
    """Старт фонового потока с единственным loop для LightRAG."""
    global _rag_thread
    with _start_lock:
        if _rag_thread is not None and _rag_thread.is_alive():
            return
        _ready_event.clear()
        _boot_error.clear()
        t = threading.Thread(
            target=_rag_thread_main,
            args=(settings,),
            name="threlium-rag-loop",
            daemon=True,
        )
        _rag_thread = t
        t.start()
        ok = _ready_event.wait(timeout=120.0)
        if not ok:
            raise RuntimeError("lightrag: RAG loop thread did not become ready within 120s")
        if _boot_error:
            raise RuntimeError("lightrag: RAG loop bootstrap failed") from _boot_error[0]


def stop_rag_loop_thread(*, settings: ThreliumSettings | None = None) -> None:
    """Остановить loop: cancel work-задач, ``finalize_storages``, ``loop.stop`` с MainThread."""
    global _rag_thread, _daemon_rag, _drain_lock
    loop = _rag_loop
    th = _rag_thread
    if loop is None or th is None or not th.is_alive():
        _rag_thread = None
        _daemon_rag = None
        _drain_lock = None
        return
    shutdown_timeout = _rag_loop_shutdown_timeout_sec(settings)

    try:
        fut = asyncio.run_coroutine_threadsafe(_shutdown_rag_loop(), loop)
        fut.result(timeout=shutdown_timeout)
    except Exception as e:
        log.error("shutdown_rag_loop_failed", error=repr(e))
    finally:
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass
    th.join(timeout=shutdown_timeout + 5.0)
    _rag_thread = None
    _daemon_rag = None
    _drain_lock = None
    reset_drain_task()
