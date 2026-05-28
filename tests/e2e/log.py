"""E2e harness logging — тот же structlog-контракт, что в threlium.logutil."""
from __future__ import annotations

import os

from threlium.logutil import logger, setup_logging

# Импорт модуля может произойти до pytest_configure — setup сразу.
setup_logging(os.environ.get("THRELIUM_LOG_LEVEL", "DEBUG"))

log = logger.bind(stage="e2e")

E2E_LOG_BODY_MAX = 8000


def clip_log_body(text: str, *, max_len: int = E2E_LOG_BODY_MAX) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
