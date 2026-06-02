"""Аргументы tool-вызовов суммаризации (thread context / response buffer).

Отдельные VO на каждый tool (DDD, ``docs/TYPES.md`` § VO). См. ``summarize_tool_bridge``.
"""
from __future__ import annotations

import msgspec


class SummarizeThreadContextToolArgs(msgspec.Struct, frozen=True):
    """Сжатая сводка батча писем треда (стадия ``summarize_context``)."""

    summary: str


class SummarizeResponseBufferToolArgs(msgspec.Struct, frozen=True):
    """Структурированное наблюдение по буферу ответа (стадия ``response_observe``)."""

    observation: str


__all__ = ["SummarizeResponseBufferToolArgs", "SummarizeThreadContextToolArgs"]
