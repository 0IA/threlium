#!/usr/bin/env python3
"""Shared durable-memory write handler (docs/MEMORY_TABLE.md §1–2).

``thread_memory`` и ``global_memory`` отличаются только адресом стадии: оба
читают note из входящего ``<system>``, нормализуют через ``base.j2`` (callee
владеет форматом) и передают в enrich_fast как ЗАПРОС-эхо ``<hash@history>``
на исходящем L_M2. Для памяти ценен именно запрос: origin предзаштампован =
``reasoning`` (автор факта). L_M1 остаётся system-only durable-архивом;
LightRAG индексирует settled L_M2, не L_M1.
"""
from email.message import EmailMessage

from threlium.fsm_emit_semantic import emit_to_enrich_fast
from threlium.mime_reform import system_part_text
from threlium.prompts import render_prompt
from threlium.settings import ThreliumSettings
from threlium.types import FsmStage, PromptPath

_MEMORY_BASE_BY_STAGE: dict[FsmStage, PromptPath] = {
    FsmStage.THREAD_MEMORY: PromptPath.THREAD_MEMORY_BASE,
    FsmStage.GLOBAL_MEMORY: PromptPath.GLOBAL_MEMORY_BASE,
}


def emit_memory_note_to_enrich_fast(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    """Durable L_M1 (system) + request_echo на L_M2 → enrich_fast."""
    note = system_part_text(msg).strip()
    base_path = _MEMORY_BASE_BY_STAGE.get(stage)
    if base_path is None:
        raise RuntimeError(f"memory write: unsupported stage {stage.value!r}")
    echo_body = render_prompt(base_path, note=note).strip()
    return emit_to_enrich_fast(
        msg,
        stage,
        request_echo=echo_body,
        settings=config,
    )
