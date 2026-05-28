"""Сводка буфера ответа для LLM (response_observe / enrich_fast)."""
from __future__ import annotations

from dataclasses import dataclass

from threlium.prompts import render_prompt
from threlium.types import PromptPath

from .ops import ResponseOp, apply_response_ops


@dataclass(frozen=True)
class ChunkView:
    """Проекция одного чанка для шаблона."""

    position: int
    content: str | None
    deleted: bool


@dataclass(frozen=True)
class StateData:
    """Структурированные данные буфера для шаблонов и LLM."""

    is_empty: bool
    live_count: int
    total_chars: int
    chunks: list[ChunkView]


def build_state_data(ops: list[ResponseOp]) -> StateData:
    """Чистая структура данных из CRDT-операций (без форматирования)."""
    raw = apply_response_ops(ops)

    if not raw:
        return StateData(is_empty=True, live_count=0, total_chars=0, chunks=[])

    total_chars = 0
    live_count = 0
    chunks: list[ChunkView] = []
    for pos in sorted(raw):
        text = raw[pos]
        if text is None:
            chunks.append(ChunkView(position=pos, content=None, deleted=True))
        else:
            live_count += 1
            total_chars += len(text)
            chunks.append(ChunkView(position=pos, content=text, deleted=False))

    return StateData(
        is_empty=False,
        live_count=live_count,
        total_chars=total_chars,
        chunks=chunks,
    )


def build_state_summary(ops: list[ResponseOp]) -> str:
    """Текстовая сводка для LLM с ``[position]`` индексами (через Jinja2)."""
    data = build_state_data(ops)
    return render_prompt(
        PromptPath.RESPONSE_OBSERVE_STATE_SUMMARY,
        is_empty=data.is_empty,
        live_count=data.live_count,
        total_chars=data.total_chars,
        chunks=[
            {"position": c.position, "content": c.content, "deleted": c.deleted}
            for c in data.chunks
        ],
    ).strip()
