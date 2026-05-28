"""Structured async-safe logging: structlog + stdlib QueueHandler + JSON → journald.

Форматирование (ProcessorFormatter + JSON) выполняется в потоке-источнике на QueueHandler,
до enqueue. В очередь попадает LogRecord с готовой строкой; фоновый StreamHandler
только пишет ``%(message)s`` в stderr — без повторного ProcessorFormatter и без
кастомного prepare() для dict в record.msg.

Публичный API:
    from threlium.logutil import logger, setup_logging, shutdown_logging
"""
from __future__ import annotations

import logging
import queue
import sys
from logging.handlers import QueueHandler, QueueListener

import structlog

_listener: QueueListener | None = None

_FOREIGN_PRE_CHAIN = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.format_exc_info,
    structlog.stdlib.ExtraAdder(),
]

_LITELLM_LOGGER_NAMES: tuple[str, ...] = ("LiteLLM", "LiteLLM Proxy", "LiteLLM Router")
_FOREIGN_LOGGER_NAMES: tuple[str, ...] = ("httpx", "lightrag")


def _configure_litellm_loggers() -> None:
    """Заглушить internal LiteLLM logging (StandardLoggingPayload, callbacks).

    Threlium логирует вызовы LLM через structlog в ``litellm_client`` и stage loggers;
    ``verbose_logger`` LiteLLM при ошибке ``model_dump()`` на StandardLoggingPayload
    пишет ERROR+traceback — здесь отключаем полностью.
    """
    for name in _LITELLM_LOGGER_NAMES:
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = False
        lg.disabled = True


def setup_logging(log_level: str = "DEBUG") -> None:
    """Инициализировать logging. Идемпотентно: повторный вызов перезапускает listener."""
    global _listener

    shutdown_logging()

    _LOG_LEVEL_MAP: dict[str, int] = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    key = log_level.upper()
    if key not in _LOG_LEVEL_MAP:
        raise ValueError(f"unknown log level {log_level!r}, expected one of {sorted(_LOG_LEVEL_MAP)}")
    numeric_level = _LOG_LEVEL_MAP[key]
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(10000)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter("%(message)s"))

    queue_handler = QueueHandler(log_queue)
    queue_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
            foreign_pre_chain=_FOREIGN_PRE_CHAIN,
        )
    )

    _listener = QueueListener(log_queue, stderr_handler, respect_handler_level=False)
    _listener.start()

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(queue_handler)
    root.setLevel(numeric_level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    _configure_litellm_loggers()
    for name in _FOREIGN_LOGGER_NAMES:
        logging.getLogger(name).setLevel(logging.WARNING)


def shutdown_logging() -> None:
    """Graceful stop. Вызывать при SIGTERM / engine stop."""
    global _listener
    if _listener is not None:
        _listener.stop()
        _listener = None


logger = structlog.get_logger()
