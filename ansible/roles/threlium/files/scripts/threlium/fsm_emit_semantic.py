"""Семантические обёртки над низкоуровневым :func:`emit_transition_preserving_payload`.

Дефолты билдера (IRT из MID входа, декремент hop, копия cap) живут здесь,
а не в ``fsm_emit`` — см. ``docs/TYPES.md``.
"""
from __future__ import annotations

from email.message import EmailMessage

from threlium.fsm_emit import (
    HDR_CAPABILITIES,
    HDR_HOP_BUDGET,
    ManagedFsmHeaderPatch,
    ManagedFsmHeaderValue,
    advance_hop_budget_for_simple_step,
    emit_transition_preserving_payload,
    irt_wire_from_incoming_message_id,
)
from threlium.settings import ThreliumSettings
from threlium.types import (
    FsmStage,
    HopBudgetLine,
    MailHeaderName,
    RfcInReplyToWire,
    ThreliumCapabilitiesBudgetLine,
)

def managed_patch_simple_fsm_step(
    incoming: EmailMessage, settings: ThreliumSettings,
) -> dict[MailHeaderName, ManagedFsmHeaderValue]:
    """Карта managed-заголовков: IRT из MID входа, декремент hop, копия cap."""
    patch: dict[MailHeaderName, ManagedFsmHeaderValue] = {}

    irt = irt_wire_from_incoming_message_id(incoming)
    if irt is not None and irt.value.strip():
        patch[MailHeaderName.IN_REPLY_TO] = irt

    patch[MailHeaderName.HOP_BUDGET] = advance_hop_budget_for_simple_step(
        HopBudgetLine.parse(incoming.get(HDR_HOP_BUDGET)), settings
    )

    cap = ThreliumCapabilitiesBudgetLine.parse(incoming.get(HDR_CAPABILITIES))
    if cap.value.strip():
        patch[MailHeaderName.CAPABILITIES] = cap

    return patch


def emit_transition_simple_step_preserving_payload(
    incoming: EmailMessage,
    *,
    to_addr: FsmStage,
    from_stage: FsmStage,
    settings: ThreliumSettings,
) -> EmailMessage:
    """Переход с сохранением тела; IRT / hop / cap как простой шаг."""
    return emit_transition_preserving_payload(
        incoming,
        to_addr=to_addr,
        from_stage=from_stage,
        managed_headers=managed_patch_simple_fsm_step(incoming, settings),
    )


def emit_transition_egress_terminal_with_route_irt_preserving_payload(
    incoming: EmailMessage,
    *,
    to_addr: FsmStage,
    from_stage: FsmStage,
    reply_to_mid: RfcInReplyToWire,
    settings: ThreliumSettings,
) -> EmailMessage:
    """Терминальный egress с каналом из route: In-Reply-To с route; hop/cap как простой шаг."""
    patch = managed_patch_simple_fsm_step(incoming, settings)
    patch[MailHeaderName.IN_REPLY_TO] = reply_to_mid
    return emit_transition_preserving_payload(
        incoming,
        to_addr=to_addr,
        from_stage=from_stage,
        managed_headers=patch,
    )


def managed_patch_subagent_push_to_ingress(
    incoming: EmailMessage,
    *,
    hop_budget: HopBudgetLine,
    capabilities: ThreliumCapabilitiesBudgetLine,
) -> ManagedFsmHeaderPatch:
    """subagent_intent → ingress: непрерывный IRT + изолированные hop/cap."""
    patch: ManagedFsmHeaderPatch = {
        MailHeaderName.HOP_BUDGET: hop_budget,
        MailHeaderName.CAPABILITIES: capabilities,
    }
    irt = irt_wire_from_incoming_message_id(incoming)
    if irt is not None and irt.value.strip():
        patch = {MailHeaderName.IN_REPLY_TO: irt, **patch}
    return patch
