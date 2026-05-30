"""Сбор task-операций из IRT-цепочки (весь фрейм текущего субагента, header-free изоляция).

IRT непрерывен (инвариант FSM) → идём от листа до начала текущего фрейма, собирая задачи
всех ходов пользователя = «общее направление треда». Изоляция субагента — единым маркерным
обходом :func:`iter_irt_ancestors_filtered`: учитываются только снимки своего уровня, до
границы фрейма (первый незакрытый ``subagent_intent``); вложенные субагенты пропускаются —
у каждого уровня свой ledger. ``stop_at_route`` НЕ используется: ledger переживает ходы
пользователя внутри фрейма.
"""
from __future__ import annotations

from threlium.mime_reform import (
    EnrichPartId,
    email_message_from_path,
    extract_part_by_content_id,
    extract_plain_body,
)
from threlium.thread_context_filter import iter_irt_ancestors_filtered
from threlium.types import FsmStage, NotmuchMessageIdInner

from .ops import TaskOp, parse_task_init_op, parse_tasks_upsert_op


def collect_task_ops(start_inner: NotmuchMessageIdInner) -> list[TaskOp]:
    """Task-операции текущего фрейма в хронологическом порядке (корень → лист).

    Фрейм определяется маркерным балансом IRT (header-free), а не ``X-Threlium-Hop-Budget``.
    Источники op: письма ``enrich → reasoning`` (MIME ``<task-init>``) и durable письма
    ``→ tasks_upsert`` (JSON tool-args в теле).
    """
    frame = list(iter_irt_ancestors_filtered(start_inner))
    frame.reverse()

    ops: list[TaskOp] = []
    for snap in frame:
        if snap.is_sent_from_fsm_stage(FsmStage.ENRICH):
            e = email_message_from_path(snap.path)
            raw = extract_part_by_content_id(e, EnrichPartId.TASK_INIT)
            if raw:
                op = parse_task_init_op(raw, message_id_inner=snap.message_id_inner)
                if op is not None:
                    ops.append(op)
        elif snap.is_addressed_to_fsm_stage(FsmStage.TASKS_UPSERT):
            msg = email_message_from_path(snap.path)
            body = extract_plain_body(msg)
            op2 = parse_tasks_upsert_op(body, message_id_inner=snap.message_id_inner)
            if op2 is not None:
                ops.append(op2)

    return ops
