#!/usr/bin/env python3
"""subagent_end@localhost → ingress@localhost: маркер завершения субагента.

Копирует hop 1-в-1 с предка перед соответствующим subagent_intent
(обычно enrich/ingress родителя до делегирования). Стоимость работы субагента
не вычитается: у субагента был изолированный бюджет, родитель «на паузе».
"""
from email.message import EmailMessage

from threlium.settings import ThreliumSettings
from threlium.fsm_emit import (
    emit_transition_preserving_payload,
    irt_wire_from_incoming_message_id,
)
from threlium.irt_subagent_classifier import (
    find_matching_subagent_intent_ancestor,
    hop_from_intent_parent,
)
from threlium.logutil import logger
from threlium.types import (
    FsmStage,
    MailHeaderName,
    NotmuchMessageIdInner,
    RfcMessageIdWire,
)

log = logger.bind(stage="subagent_end")


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    mid_w = RfcMessageIdWire.parse_present_from_email(msg, MailHeaderName.MESSAGE_ID)
    inner = NotmuchMessageIdInner.from_optional_wire(mid_w)
    if inner is None:
        raise RuntimeError(
            "FSM-инвариант: subagent_end требует непустой Message-ID"
        )

    ancestor = find_matching_subagent_intent_ancestor(inner)
    hop = hop_from_intent_parent(ancestor)

    irt = irt_wire_from_incoming_message_id(msg)
    patch: dict[MailHeaderName, object] = {MailHeaderName.HOP_BUDGET: hop}
    if irt is not None and irt.value.strip():
        patch[MailHeaderName.IN_REPLY_TO] = irt

    log.info("transition_to_ingress", hop=hop.value, message_id=mid_w.value if mid_w else None)

    return emit_transition_preserving_payload(
        msg,
        to_addr=FsmStage.INGRESS,
        from_stage=stage,
        managed_headers=patch,  # type: ignore[arg-type]
    )
