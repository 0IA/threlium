"""LightRAG runner wire VO."""
from __future__ import annotations

import msgspec

from ._core import _OptionalStripEmpty


class LightragWorkerBatchThreadIdKey(_OptionalStripEmpty):
    """Ключ ``threadid`` в батче lightrag-worker (notmuch → строка треда)."""


class LightragLiteLlmCompletionBody(_OptionalStripEmpty):
    """Текст ``choices[0].message.content`` в LLM-обёртке lightrag."""


class LightragChunkRecord(msgspec.Struct, frozen=True):
    """Один чанк для ``chunking_func`` LightRAG (перед ``dict`` на границе библиотеки)."""

    tokens: int
    content: str
    chunk_order_index: int
