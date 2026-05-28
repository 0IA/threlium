"""Редукция response-операций в итоговый текст ответа."""
from __future__ import annotations

from threlium.prompts import render_prompt
from threlium.types import PromptPath

from .ops import ResponseOp, apply_response_ops


def reduce_ops(ops: list[ResponseOp]) -> str:
    """Линейное применение операций: AppendOp добавляет, EditOp правит/удаляет.

    Сборка чанков в итоговый текст делегирована шаблону
    ``response_finalize/chunk_assembly.j2`` — оператор контролирует
    разделители, обёртки и порядок.
    """
    chunks = apply_response_ops(ops)

    parts: list[str] = []
    for pos in sorted(chunks):
        text = chunks[pos]
        if text is not None:
            parts.append(text)

    return render_prompt(PromptPath.RESPONSE_FINALIZE_CHUNK_ASSEMBLY, parts=parts).strip()
