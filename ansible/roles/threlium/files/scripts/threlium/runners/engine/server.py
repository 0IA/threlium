"""UNIX-stream сервер движка FSM (JSON по строке на соединение).

Протокол по одной JSON-строке на соединение — ``threlium.types.EngineWireRequest`` /
``EngineWireOk`` / ``EngineWireError`` + ``msgspec.json`` (см. ``docs/TYPES.md``).
"""
from __future__ import annotations

import signal
import socketserver
import threading
import traceback

from threlium.litellm_tool_spec import warm_tool_specs
from threlium.logutil import logger, setup_logging, shutdown_logging
from threlium.prompts import init_prompts_root, warm_prompt_templates
from threlium.settings import ThreliumSettings, load_settings
from threlium.types import (
    EngineWireError,
    EngineWireOk,
    FsmStage,
    NotmuchThreadScopeId,
)

from threlium.runners.engine.fsm import process_thread_message
from threlium.runners.engine.paths import engine_socket_path
from threlium.runners.engine.wire_io import (
    decode_engine_wire_request,
    encode_wire_line,
    read_wire_line,
)
from threlium.runners.lightrag import (
    schedule_bootstrap_knowledge,
    start_rag_loop_thread,
    stop_rag_loop_thread,
)
from threlium.systemd_notify import notify_ready, notify_status, notify_stopping
from threlium.types.litellm_correlation_header import LitellmCorrelationHeader
from threlium.types.litellm_call_site import LitellmCallSite
from threlium.types.systemd_status import SystemdStatusBody


GLOBAL_CFG: ThreliumSettings | None = None


class _EngineRequestHandler(socketserver.StreamRequestHandler):
    """Один запрос = одна строка JSON → ``process_thread_message``."""

    def handle(self) -> None:
        assert GLOBAL_CFG is not None
        try:
            line = read_wire_line(self.rfile)
            if not line:
                return
            wire = decode_engine_wire_request(line)
            stage_vo = FsmStage.parse(wire.stage)
            tid = NotmuchThreadScopeId.from_notmuch_thread_attr(wire.thread_id)
            if tid is None:
                raise ValueError(f"Invalid notmuch thread id: {wire.thread_id!r}")
            process_thread_message(stage_vo, tid, GLOBAL_CFG)
            self.wfile.write(encode_wire_line(EngineWireOk(status="ok")))
        except Exception as e:
            err = EngineWireError(
                status="error",
                message=str(e),
                traceback=traceback.format_exc(),
            )
            self.wfile.write(encode_wire_line(err))


def main() -> None:
    """Точка входа ``python -m threlium.runners.engine``."""
    global GLOBAL_CFG
    GLOBAL_CFG = load_settings()
    setup_logging(GLOBAL_CFG.log_level)
    # Единый дамп ФАКТИЧЕСКОГО (собранного: yaml+env+defaults) конфига на старте — чтобы при отладке
    # не гадать «yaml говорит X, env Y, default Z»: видно итоговые значения, которыми реально работает
    # движок. lightrag/e2e — без секретов (батчи, query-режим, сторы, флаги); litellm НЕ дампим (ключи).
    logger.bind(stage="engine").info(
        "engine_effective_config",
        log_level=GLOBAL_CFG.log_level,
        home=str(GLOBAL_CFG.home),
        lightrag=GLOBAL_CFG.lightrag.model_dump(mode="json"),
        e2e=GLOBAL_CFG.e2e.model_dump(mode="json"),
    )
    init_prompts_root(GLOBAL_CFG.home)
    # Прогрев на старте (один раз, требует init_prompts_root): (1) скомпилировать ВСЕ jinja-шаблоны в кэш
    # окружения — синтаксис всплывает на boot, нет парса .j2 в первом вызове; (2) собрать+кэшировать
    # jsonschema-валидаторы tool-spec — tool-call'ы не платят ~1мс check_schema на каждый запрос (см.
    # prompts.warm_prompt_templates / litellm_tool_spec._cached_validator). Рендер остаётся per-call.
    warm_prompt_templates()
    warm_tool_specs(GLOBAL_CFG)
    notify_status(SystemdStatusBody.engine_home_configured())
    notify_status(SystemdStatusBody.engine_config_loaded())
    notify_status(SystemdStatusBody.engine_starting_lightrag_loop())
    start_rag_loop_thread(GLOBAL_CFG)

    bootstrap_correlation: dict[str, str] | None = None
    if GLOBAL_CFG.e2e.litellm_route_correlation:
        bootstrap_correlation = {
            LitellmCorrelationHeader.THREAD_ROOT_MID.value: "e2e-bootstrap",
            LitellmCorrelationHeader.CALL_SITE.value: LitellmCallSite.LIGHTRAG_INDEX.value,
        }

    sock_path = engine_socket_path(GLOBAL_CFG.home)
    notify_status(SystemdStatusBody.engine_preparing_socket(sock_path))
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    if sock_path.exists():
        sock_path.unlink()

    server = socketserver.ThreadingUnixStreamServer(
        str(sock_path),
        _EngineRequestHandler,
    )

    # ``BaseServer.shutdown()`` ждёт завершения ``serve_forever()`` (stdlib). Если
    # вызвать её из обработчика сигнала на **том же** потоке, где крутится
    # ``serve_forever()`` — взаимная блокировка: цикл не возвращается, событие
    # не выставляется, systemd уходит в TimeoutStopSec → SIGKILL.
    _sig_shutdown_state: dict[str, bool] = {"started": False}
    _sig_shutdown_lock = threading.Lock()

    def _stop(signum: int, frame: object | None) -> None:
        with _sig_shutdown_lock:
            if _sig_shutdown_state["started"]:
                return
            _sig_shutdown_state["started"] = True
        notify_stopping()
        notify_status(SystemdStatusBody.engine_stopping())

        def _run_shutdown() -> None:
            try:
                server.shutdown()
            except OSError:
                pass

        threading.Thread(
            target=_run_shutdown,
            name="threlium-engine-shutdown",
            daemon=True,
        ).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    notify_status(SystemdStatusBody.engine_listening_on(sock_path))
    notify_ready()
    notify_status(SystemdStatusBody.engine_idle_waiting_fsm_requests())
    # Индексация knowledge/ — ПОСЛЕ READY: не блокирует старт под Type=notify и не падает
    # по per-call LLM timeout. Фоновая задача на RAG-loop со своим bootstrap_timeout_sec.
    schedule_bootstrap_knowledge(GLOBAL_CFG, correlation=bootstrap_correlation)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if sock_path.exists():
            sock_path.unlink()
        stop_rag_loop_thread(settings=GLOBAL_CFG)
        shutdown_logging()
