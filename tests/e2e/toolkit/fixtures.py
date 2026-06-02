"""Injectable e2e mail bodies and markers."""
from __future__ import annotations

from .constants import (
    E2E_CTX_TRIM_HEAD_MARKER,
    E2E_CTX_TRIM_TAIL_MARKER,
    E2E_SUM_ORIG_HEAD_MARKER,
    E2E_SUM_ORIG_PAD_MARKER,
    _E2E_DENSE_CORR_SEGMENTS,
)

def e2e_dense_threlium_ctx_body(*, head: str, correlation_key: str) -> str:
    """Текст письма: ``head`` + много строк с тем же ``correlation_key`` подряд.

    Чанкер LightRAG добавляет к каждому чанку строку ``Subject: …`` (см.
    ``threlium/lightrag_chunking.py``); стабы ``/embeddings`` в основном матчятся по ней.
    Плотные повторяющиеся строки остаются полезны для попадания маркера в чанки и для журнала WireMock.
    Для e2e-корреляции LiteLLM ``correlation_key`` в сиде State совпадает с canonical
    thread-root MID (см. :func:`e2e_thread_root_mid_for_message_id`).
    """
    lines = [head.rstrip("\n")] if head.strip() else []
    lines.extend(
        f"e2e_ctx_seg_{i:03d} {correlation_key}" for i in range(_E2E_DENSE_CORR_SEGMENTS)
    )
    return "\n".join(lines) + "\n"


def e2e_oversized_context_trim_body(
    *,
    head: str,
    correlation_key: str,
    pad_chars: int = 60_000,
) -> str:
    """Тело письма для e2e trim: HEAD в начале, TAIL в конце, между ними padding."""
    pad = max(0, pad_chars)
    core = (
        f"{E2E_CTX_TRIM_HEAD_MARKER}\n"
        f"{head.rstrip()}\n"
        f"{'X' * pad}\n"
        f"{E2E_CTX_TRIM_TAIL_MARKER}\n"
        f"e2e_ctx_tail {correlation_key}\n"
    )
    return core


def e2e_oversized_context_trim_prior_turn_body(
    *,
    head: str,
    correlation_key: str,
    pad_chars: int = 25_000,
) -> str:
    """Предыдущий ход треда для trim e2e: HEAD + padding (без TAIL — он на текущем ходе)."""
    pad = max(0, pad_chars)
    return (
        f"{E2E_CTX_TRIM_HEAD_MARKER}\n"
        f"{head.rstrip()}\n"
        f"{'X' * pad}\n"
        f"e2e_ctx_prior {correlation_key}\n"
    )


def e2e_oversized_context_trim_current_turn_body(
    *,
    head: str,
    correlation_key: str,
) -> str:
    """Текущий ход для trim e2e: TAIL-маркер (HEAD/pad уже в unified с прошлого ingress)."""
    return (
        f"{E2E_CTX_TRIM_TAIL_MARKER}\n"
        f"{head.rstrip()}\n"
        f"e2e_ctx_tail {correlation_key}\n"
    )


def e2e_summarize_overflow_inject_body(
    *,
    head: str,
    correlation_key: str,
    pad_chars: int = 25_000,
) -> str:
    """Тело для e2e summarize overflow: HEAD + длинный PAD (исключается после суммаризации)."""
    pad = max(0, pad_chars)
    return (
        f"{E2E_SUM_ORIG_HEAD_MARKER}\n"
        f"{head.rstrip()}\n"
        f"{E2E_SUM_ORIG_PAD_MARKER}\n"
        f"{'P' * pad}\n"
        f"e2e_sum_tail {correlation_key}\n"
    )
