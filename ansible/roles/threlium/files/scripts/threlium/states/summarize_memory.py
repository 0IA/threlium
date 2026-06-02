#!/usr/bin/env python3
"""summarize_memory@localhost: стадия-хранитель итога суммаризации.

Аналог thread_memory — ничего не делает кроме возврата в enrich.
Письмо To: summarize_memory@ несёт ``<history>``-часть (сводку от summarize_context)
и остаётся в Maildir, поэтому попадает в ``<unified-mail-context>`` по предикату
``message_has_history`` (оригиналы при этом помечены ``context_summarized`` и выпадают).
"""
from __future__ import annotations

from email.message import EmailMessage

from threlium.fsm_emit import build_fsm_step_to_stage
from threlium.mime_reform import last_history_part_text
from threlium.settings import ThreliumSettings
from threlium.types import FsmStage


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    # Сводка от summarize_context на <history>; без неё — FSM-инвариант (CONTEXT_CONTRACT §3).
    summary_for_enrich = last_history_part_text(msg)
    return build_fsm_step_to_stage(
        msg,
        to_addr=FsmStage.ENRICH,
        from_stage=stage,
        history=summary_for_enrich,
        settings=config,
    )
