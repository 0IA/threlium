#!/usr/bin/env python3
"""global_memory@localhost → enrich_fast@localhost (docs/MEMORY_TABLE.md §2).

Записывает note в Maildir (durable, settled при fdm insert) и передаёт
его как ``<memory-note>`` MIME-часть в enrich_fast для мгновенного
отражения в контексте reasoning. Полная RAG-индексация — async,
доступна на следующем reflect-цикле.
"""
from email.message import EmailMessage

from threlium.fsm_emit import build_fsm_multipart_to_stage
from threlium.mime_reform import EnrichPartId, extract_plain_body
from threlium.settings import ThreliumSettings
from threlium.types import FsmStage


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    note = extract_plain_body(msg).strip()
    return build_fsm_multipart_to_stage(
        msg,
        to_addr=FsmStage.ENRICH_FAST,
        from_stage=stage,
        parts=[(EnrichPartId.MEMORY_NOTE, f"[global_memory recorded] {note}")],
        settings=config,
    )
