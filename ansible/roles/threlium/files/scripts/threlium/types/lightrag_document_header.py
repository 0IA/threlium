"""Заголовки синтетических RFC822-документов для LightRAG ``rag.ainsert()``.

Не являются заголовками реальных FSM-писем — используются только в
``lightrag_ingest.py`` и ``lightrag_chunking.py`` для метаданных
текстовых документов, оформленных как RFC822.
"""
from __future__ import annotations

from enum import StrEnum


class LightragDocumentHeader(StrEnum):
    """Wire-имена заголовков в синтетических документах LightRAG."""

    THREAD_ID = "X-Threlium-Thread-Id"
    CHUNK_INDEX = "X-Threlium-LightRAG-Chunk"
