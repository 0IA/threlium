#!/usr/bin/env python3
"""egress_router: IRT-based depth classifier (план рефакторинга §1).

response_finalize → depth > 0 → subagent_end (незакрытый субагент).
response_finalize → depth == 0 → egress_* (внешний канал, корень).
cli_hitl_out → egress_* (HITL bridge, без RA).
"""
from email.message import EmailMessage

from threlium.fsm_emit_semantic import (
    emit_transition_simple_step_preserving_payload,
)
from threlium.ingress_route_resolve import (
    ResolvedRoute,
    egress_addr_for_channel,
    resolve_route_for_egress_fsm_from_email,
)
from threlium.irt_subagent_classifier import classify_subagent_depth_from_email
from threlium.logutil import logger
from threlium.types import (
    FsmStage,
    IngressRouterResolvedChannelSlug,
    MailHeaderName,
)
from threlium.types.nm_addressed import email_message_sent_from_fsm_stage
from threlium.settings import ThreliumSettings

log = logger.bind(stage="egress_router")


def _require_resolved_channel(route: ResolvedRoute, *, ctx: str) -> IngressRouterResolvedChannelSlug:
    ch = route.channel
    if not ch.value:
        raise RuntimeError(
            f"egress_router: {ctx}: resolved route has empty channel (route={route!r})"
        )
    return ch


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    from_finalize = email_message_sent_from_fsm_stage(msg, FsmStage.RESPONSE_FINALIZE)
    from_hitl = email_message_sent_from_fsm_stage(msg, FsmStage.CLI_HITL_OUT)

    if from_finalize:
        depth_result = classify_subagent_depth_from_email(msg)
        if depth_result.depth > 0:
            log.info("subagent_depth_redirect", depth=depth_result.depth)
            return emit_transition_simple_step_preserving_payload(
                msg,
                to_addr=FsmStage.SUBAGENT_END,
                from_stage=stage,
                settings=config,
            )

    route = resolve_route_for_egress_fsm_from_email(msg)

    if from_hitl:
        channel = _require_resolved_channel(route, ctx="cli_hitl_out(HITL)")
        addr = egress_addr_for_channel(channel)
        log.info("hitl_bridge", channel=channel.value, target=addr.rfc822_mailbox)
        return emit_transition_simple_step_preserving_payload(
            msg,
            to_addr=addr,
            from_stage=stage,
            settings=config,
        )

    if from_finalize:
        channel = _require_resolved_channel(
            route, ctx="response_finalize(external reply, depth==0)"
        )
        addr = egress_addr_for_channel(channel)
        log.info("external_reply", channel=channel.value, target=addr.rfc822_mailbox)
        # Линейный IRT-инвариант: терминальный egress тредится на свой FSM-вход
        # (response_finalize), а НЕ на route-MID. Внутренний FSM-тред остаётся
        # полностью линейным (см. docs/THREAD_MODEL.md); канал/получатель/reply_target
        # для внешнего письма резолвятся подъёмом по IRT до tag:route отдельно.
        return emit_transition_simple_step_preserving_payload(
            msg,
            to_addr=addr,
            from_stage=stage,
            settings=config,
        )

    from_hdr = msg.get(MailHeaderName.FROM, "<unknown>")
    channel = _require_resolved_channel(route, ctx=f"unknown From={from_hdr!r}")
    addr = egress_addr_for_channel(channel)
    return emit_transition_simple_step_preserving_payload(
        msg,
        to_addr=addr,
        from_stage=stage,
        settings=config,
    )
