"""enrich_fast@localhost → reasoning@localhost.

Быстрый цикл обратной связи: берёт предыдущий enriched-контекст ``E_prev``
(multipart/mixed с MIME-частями по Content-ID), добавляет/обновляет
``<response-state>`` и возвращает в reasoning без повторного RAG.
"""
from __future__ import annotations

from email.message import EmailMessage

from threlium.enrich_context import trim_context_text
from threlium.fsm_emit import emit_transition_preserving_payload
from threlium.fsm_emit_semantic import managed_patch_simple_fsm_step
from threlium.irt_chain import iter_in_reply_to_ancestors_from_inner_id
from threlium.logutil import logger
from threlium.mime_reform import (
    EnrichPartId,
    email_message_from_path,
    extract_part_by_content_id,
    replace_or_add_part,
)
from threlium.response.collect import collect_ops
from threlium.response.state_summary import build_state_summary
from threlium.settings import ThreliumSettings
from threlium.types import (
    FsmStage,
    MailHeaderName,
    NotmuchMessageIdInner,
    RfcMessageIdWire,
)

log = logger.bind(stage="enrich_fast")


def _find_e_prev(start_inner: NotmuchMessageIdInner) -> EmailMessage | None:
    """Найти ``E_prev``: первый предок, адресованный reasoning@localhost."""
    chain = iter_in_reply_to_ancestors_from_inner_id(start_inner)
    for snap in chain:
        if snap.is_addressed_to_fsm_stage(FsmStage.REASONING):
            return email_message_from_path(snap.path)
    return None


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    mid_w = RfcMessageIdWire.parse_present_from_email(msg, MailHeaderName.MESSAGE_ID.value)
    inner = NotmuchMessageIdInner.from_optional_wire(mid_w)
    if inner is None:
        raise RuntimeError("enrich_fast: no Message-ID on incoming message")

    e_prev = _find_e_prev(inner)
    if e_prev is None:
        raise RuntimeError(
            "enrich_fast: could not find previous enriched message "
            "(addressed to reasoning@localhost) in IRT chain"
        )

    ops = collect_ops(inner)
    summary = build_state_summary(ops)

    limit = config.enrich.context_max_chars
    trimmed_summary = trim_context_text(summary, limit)

    updated = replace_or_add_part(
        e_prev,
        EnrichPartId.RESPONSE_STATE,
        trimmed_summary,
    )

    _RELAY_PART_IDS = frozenset({EnrichPartId.PLAN_STATE, EnrichPartId.MEMORY_NOTE, EnrichPartId.OBSERVATION_NOTE})
    relayed: list[str] = []
    for part_id in _RELAY_PART_IDS:
        text = extract_part_by_content_id(msg, part_id)
        if text is not None:
            trimmed = trim_context_text(text.strip(), limit)
            if trimmed:
                updated = replace_or_add_part(updated, part_id, trimmed)
                relayed.append(part_id.value)

    log.info(
        "spliced_response_state",
        ops_count=len(ops),
        summary_chars=len(trimmed_summary),
        relayed_parts=relayed or None,
        message_id=mid_w.value if mid_w else None,
    )

    return emit_transition_preserving_payload(
        updated,
        to_addr=FsmStage.REASONING,
        from_stage=stage,
        managed_headers=managed_patch_simple_fsm_step(msg, config),
    )
