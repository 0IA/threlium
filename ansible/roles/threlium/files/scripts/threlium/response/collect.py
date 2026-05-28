"""Сбор response-операций из IRT-цепочки (leaf → tag:route boundary)."""
from __future__ import annotations

import json

from threlium.irt_chain import IrtAncestorSnapshot, iter_in_reply_to_ancestors_from_inner_id
from threlium.mime_reform import extract_plain_body, email_message_from_path
from threlium.types import FsmStage, NotmuchMessageIdInner, NotmuchTag

from .ops import AppendOp, EditOp, ResponseOp

_APPEND_STAGES = frozenset({FsmStage.RESPONSE_APPEND})
_EDIT_STAGES = frozenset({FsmStage.RESPONSE_EDIT})
_ALL_RESPONSE_STAGES = _APPEND_STAGES | _EDIT_STAGES


def _is_response_stage(snap: IrtAncestorSnapshot) -> bool:
    return any(snap.is_sent_from_fsm_stage(s) for s in _ALL_RESPONSE_STAGES)


def _is_append_stage(snap: IrtAncestorSnapshot) -> bool:
    return any(snap.is_sent_from_fsm_stage(s) for s in _APPEND_STAGES)


def _is_edit_stage(snap: IrtAncestorSnapshot) -> bool:
    return any(snap.is_sent_from_fsm_stage(s) for s in _EDIT_STAGES)


def _parse_edit_body(snap: IrtAncestorSnapshot) -> tuple[int, str | None]:
    """JSON body EditOp: ``{position: int, new_content: str | null}``."""
    msg = email_message_from_path(snap.path)
    raw = extract_plain_body(msg).strip()
    data = json.loads(raw)
    position = int(data["position"])
    new_content = data.get("new_content")
    if new_content is not None:
        new_content = str(new_content)
    return position, new_content


def _read_append_content(snap: IrtAncestorSnapshot) -> str:
    msg = email_message_from_path(snap.path)
    return extract_plain_body(msg).strip()


def collect_ops(start_inner: NotmuchMessageIdInner) -> list[ResponseOp]:
    """Собрать response-операции из IRT-цепочки до ``tag:route``.

    Возвращает хронологический список (корень → лист).
    ``AppendOp.position`` — 0-based индекс среди append-операций.
    """
    chain = iter_in_reply_to_ancestors_from_inner_id(start_inner)

    relevant: list[IrtAncestorSnapshot] = []
    for snap in chain:
        if NotmuchTag.ROUTE.value in snap.tags:
            break
        if _is_response_stage(snap):
            relevant.append(snap)

    relevant.reverse()

    ops: list[ResponseOp] = []
    append_position = 0
    for snap in relevant:
        if _is_append_stage(snap):
            content = _read_append_content(snap)
            if content:
                ops.append(
                    AppendOp(
                        position=append_position,
                        content=content,
                        message_id_inner=snap.message_id_inner,
                    )
                )
                append_position += 1
        elif _is_edit_stage(snap):
            target_position, new_content = _parse_edit_body(snap)
            ops.append(
                EditOp(
                    target_position=target_position,
                    new_content=new_content,
                    message_id_inner=snap.message_id_inner,
                )
            )

    return ops
