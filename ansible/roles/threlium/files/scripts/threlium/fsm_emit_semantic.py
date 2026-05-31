"""Семантические обёртки над низкоуровневым :func:`emit_transition_preserving_payload`.

Дефолты билдера (IRT из MID входа, декремент hop) живут здесь,
а не в ``fsm_emit`` — см. ``docs/TYPES.md``.
"""
from __future__ import annotations

from email.message import EmailMessage

from threlium.fsm_emit import (
    HDR_HOP_BUDGET,
    ManagedFsmHeaderPatch,
    ManagedFsmHeaderValue,
    advance_hop_budget_for_simple_step,
    emit_transition_preserving_payload,
    irt_wire_from_incoming_message_id,
)
from threlium.settings import ThreliumSettings
from threlium.types import FsmStage, HopBudgetLine, MailHeaderName


def managed_patch_simple_fsm_step(
    incoming: EmailMessage, settings: ThreliumSettings,
) -> dict[MailHeaderName, ManagedFsmHeaderValue]:
    """Карта managed-заголовков: IRT из MID входа, декремент hop."""
    patch: dict[MailHeaderName, ManagedFsmHeaderValue] = {}

    irt = irt_wire_from_incoming_message_id(incoming)
    if irt is not None and irt.value.strip():
        patch[MailHeaderName.IN_REPLY_TO] = irt

    patch[MailHeaderName.HOP_BUDGET] = advance_hop_budget_for_simple_step(
        HopBudgetLine.parse(incoming.get(HDR_HOP_BUDGET)), settings
    )

    return patch


def emit_transition_simple_step_preserving_payload(
    incoming: EmailMessage,
    *,
    to_addr: FsmStage,
    from_stage: FsmStage,
    settings: ThreliumSettings,
) -> EmailMessage:
    """Переход с сохранением тела; IRT / hop как простой шаг."""
    return emit_transition_preserving_payload(
        incoming,
        to_addr=to_addr,
        from_stage=from_stage,
        managed_headers=managed_patch_simple_fsm_step(incoming, settings),
    )


def managed_patch_subagent_push_to_ingress(
    incoming: EmailMessage,
    *,
    hop_budget: HopBudgetLine,
) -> ManagedFsmHeaderPatch:
    """subagent_intent → ingress: непрерывный IRT + изолированный hop."""
    patch: ManagedFsmHeaderPatch = {MailHeaderName.HOP_BUDGET: hop_budget}
    irt = irt_wire_from_incoming_message_id(incoming)
    if irt is not None and irt.value.strip():
        patch = {MailHeaderName.IN_REPLY_TO: irt, **patch}
    return patch
