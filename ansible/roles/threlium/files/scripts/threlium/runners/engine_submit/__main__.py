"""``python -m threlium.runners.engine_submit`` — submit ``threlium-work@%i`` в engine."""
from __future__ import annotations

import os
import sys

from threlium.enginewire import (
    EngineWireError,
    EngineWireOk,
    EngineWireRequest,
    WorkStatusBody,
)
from threlium.logutil import logger
from threlium.runners.engine_submit.client import resolve_engine_socket_path, submit_to_engine
from threlium.systemd_notify import ensure_systemd_user_env, notify_status


def main(argv: list[str] | None = None) -> int:
    ensure_systemd_user_env()

    args = argv if argv is not None else sys.argv
    if len(args) < 2:
        print("engine_submit: missing instance (%i)", file=sys.stderr)
        return 1

    instance = args[1]
    try:
        req = EngineWireRequest.from_work_instance(instance)
    except ValueError as e:
        logger.error("submit_invalid_work_instance", instance=instance, exc_info=e)
        print(f"engine_submit: {e}", file=sys.stderr)
        return 1

    try:
        sock_path = resolve_engine_socket_path(os.environ.get("THRELIUM_HOME"))
    except ValueError as e:
        logger.error("submit_resolve_socket_failed", exc_info=e)
        print(f"engine_submit: {e}", file=sys.stderr)
        return 1

    notify_status(WorkStatusBody.work_waiting_for_engine(work_instance=instance))

    try:
        wire = submit_to_engine(sock_path, req)
    except OSError as exc:
        logger.error("submit_engine_connect_failed", instance=instance, sock_path=str(sock_path), exc_info=exc)
        notify_status(WorkStatusBody.work_failed_socket(work_instance=instance))
        print(
            f"engine_submit: cannot connect to engine at {sock_path} "
            "(is threlium-engine.service running?)",
            file=sys.stderr,
        )
        return 1
    except ValueError as e:
        logger.error("submit_engine_protocol_failed", instance=instance, exc_info=e)
        notify_status(WorkStatusBody.work_failed_socket(work_instance=instance))
        print(f"engine_submit: {e}", file=sys.stderr)
        return 1

    if isinstance(wire, EngineWireError):
        notify_status(WorkStatusBody.work_failed_engine_error(work_instance=instance))
        print(f"engine_submit: engine error for {instance}:", file=sys.stderr)
        detail = wire.traceback or wire.message
        if detail:
            print(detail, file=sys.stderr)
        return 1

    if isinstance(wire, EngineWireOk):
        notify_status(WorkStatusBody.work_done(work_instance=instance))
        return 0

    notify_status(WorkStatusBody.work_failed_socket(work_instance=instance))
    print(f"engine_submit: unexpected engine response for {instance}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
