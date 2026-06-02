#!/usr/bin/env python3
"""summarize_memory@localhost: стадия-хранитель итога суммаризации.

Письмо To: summarize_memory@ от summarize_context несёт ``<history>`` (сводку, durable) и
``<system>`` (канонический ``user_query``). Сводка остаётся в Maildir и попадает в
``<unified-mail-context>`` по предикату ``message_has_history`` (оригиналы при этом помечены
``context_summarized`` и выпадают). В enrich дренируется именно ``user_query`` как ``<history>``:
re-trigger enrich повторяет тот же ход пользователя (суммаризация его не меняет), а сводку
видит из unified.
"""
from __future__ import annotations

from email.message import EmailMessage

from threlium.fsm_emit import build_fsm_step_to_stage
from threlium.mime_reform import system_part_text
from threlium.settings import ThreliumSettings
from threlium.types import FsmStage


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    # user_query релеится summarize_context в <system>; возвращаем его enrich как <history>,
    # чтобы re-trigger прочитал тот же user message (last_history_text). Сводка durable на
    # письме summarize_context уже в unified (CONTEXT_CONTRACT §5: цикл summarize не меняет ход).
    user_query = system_part_text(msg)
    return build_fsm_step_to_stage(
        msg,
        to_addr=FsmStage.ENRICH,
        from_stage=stage,
        history=user_query,
        settings=config,
    )
