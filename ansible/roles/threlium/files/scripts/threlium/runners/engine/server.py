"""UNIX-stream сервер движка FSM (JSON по строке на соединение).

Протокол по одной JSON-строке на соединение — ``threlium.types.EngineWireRequest`` /
``EngineWireOk`` / ``EngineWireError`` + ``msgspec.json`` (см. ``docs/TYPES.md``).
"""
from __future__ import annotations

import signal
import socketserver
import threading
import traceback
from pathlib import Path

from threlium.logutil import setup_logging, shutdown_logging
from threlium.prompts import init_prompts_root
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
from threlium.runners.lightrag import start_rag_loop_thread, stop_rag_loop_thread
from threlium.runners.lightrag._bootstrap import bootstrap_knowledge_dir
from threlium.runners.lightrag import run_rag_coroutine, daemon_lightrag
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
    init_prompts_root(GLOBAL_CFG.home)
    notify_status(SystemdStatusBody.engine_home_configured())
    notify_status(SystemdStatusBody.engine_config_loaded())
    notify_status(SystemdStatusBody.engine_starting_lightrag_loop())
    start_rag_loop_thread(GLOBAL_CFG)

    rag = daemon_lightrag()
    if rag is not None:
        bootstrap_correlation: dict[str, str] | None = None
        if GLOBAL_CFG.e2e.litellm_route_correlation:
            bootstrap_correlation = {
                LitellmCorrelationHeader.THREAD_ROOT_MID.value: "e2e-bootstrap",
                LitellmCorrelationHeader.CALL_SITE.value: LitellmCallSite.LIGHTRAG_INDEX.value,
            }
        run_rag_coroutine(
            bootstrap_knowledge_dir(rag, GLOBAL_CFG),
            settings=GLOBAL_CFG,
            correlation=bootstrap_correlation,
        )

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
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if sock_path.exists():
            sock_path.unlink()
        stop_rag_loop_thread(settings=GLOBAL_CFG)
        shutdown_logging()
