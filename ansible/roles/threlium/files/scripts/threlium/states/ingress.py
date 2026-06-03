#!/usr/bin/env python3
"""ingress@localhost (INGRESS_ROUTER): правила SUBAGENT_TABLE §ingress_router.

`docs/INDEX.md` §8: hard-fail на нарушение FSM-инварианта,
graceful обработка только для «новый внешний тред» (Case 1). Стадии
не индексируют — `notmuch insert` делает fdm, см. `docs/INDEX.md` §1/§4.

Fail-fast матрица (`docs/INDEX.md` §8):

  * Case 1 (parent не виден в notmuch) — graceful: новый внешний тред
    идёт в ``enrich`` (единственный legal-вход в ``reasoning``,
    `docs/FSM.md §2.1`); orphan-notice префиксуется в distill envelope.
  * HITL — обход предков по IRT (1–N шагов) до From: cli_hitl_out →
    ``cli_resume``.
"""
from email.message import EmailMessage

from threlium.settings import ThreliumSettings
from threlium.fsm_emit import build_fsm_step_to_stage, emit_transition_preserving_payload
from threlium.fsm_emit_semantic import (
    emit_transition_simple_step_preserving_payload,
    managed_patch_simple_fsm_step,
)
from threlium.ingress_distill import ingress_distill_llm
from threlium.logutil import logger
from threlium.mime_reform import (
    EnrichContentId,
    _make_inline_text_part,
    email_without_system_parts,
    extract_plain_body,
    ingress_external_body_text,
    ingress_pipeline_email,
    message_has_history,
    message_has_system,
    require_unique_threading_rfc822_headers,
)
from threlium import nm
from threlium.types.ingress_hitl import (
    HitlParentWithIntent,
    HitlParentWithoutIntent,
    classify_hitl_parent_notmuch,
)
from threlium.types import (
    FsmStage,
    FsmTransitionPlainSubjectLine,
    IngressDistillEnvelope,
    IngressExternalBodyText,
    IngressRouterChildMsg,
    MailHeaderName,
    OrphanNoticePrefixLine,
    RfcSubjectWire,
    bridge_channel_from_email,
)
from threlium.types.content_score import ThreliumContentScoreWire

log = logger.bind(stage="ingress")

ORPHAN_NOTICE = (
    "[Threlium notice: this message replies to a thread we don't have in "
    "our union index (parent Message-ID not found). Treating it as a new "
    "external thread.]"
)


def _prefix_body_for_distill(
    full_body: str,
    prefix_text: str | None,
) -> str:
    p = OrphanNoticePrefixLine.parse(prefix_text).value if prefix_text else ""
    user_body = full_body.strip()
    if not p:
        return user_body
    return p + "\n\n" + user_body


def _emit_to_enrich(
    msg: EmailMessage, stage: FsmStage, *, orphan: bool = False, config: ThreliumSettings,
) -> EmailMessage:
    # CONTEXT_CONTRACT §4.1/§3: distill пропускаем только для релеев, которые УЖЕ несут
    # canonical <history> (subagent_end / subagent_intent-echo) — повторный distill заменил бы
    # last_history (= ответ субагента) на свой user_query. Релеи с одним только <system>
    # (reflect refresh-промпт, error-ветки response_finalize/response_edit/tasks_upsert,
    # subagent budget-exhausted) и внешний вход <history> не несут — их distill превращает в
    # <history> для следующего enrich (см. MEMORY_TABLE §3, stub reflect_cycle/ingress_distill).
    if message_has_history(msg):
        relay = email_without_system_parts(msg) if message_has_system(msg) else msg
        # IRT из MID входа ingress (THREAD_MODEL §3); relay после strip @system
        # не несёт конверт — managed_patch на relay терял In-Reply-To (SUBAGENT_TABLE §4).
        return emit_transition_preserving_payload(
            relay,
            to_addr=FsmStage.ENRICH,
            from_stage=stage,
            managed_headers=managed_patch_simple_fsm_step(msg, config),
        )

    body_vo = ingress_external_body_text(msg)
    orphan_notice = OrphanNoticePrefixLine.parse(ORPHAN_NOTICE) if orphan else None
    distill_body = _prefix_body_for_distill(
        body_vo.value,
        orphan_notice.value if orphan_notice else None,
    )
    envelope = IngressDistillEnvelope.from_email(
        msg,
        channel=bridge_channel_from_email(msg),
        full_body=IngressExternalBodyText.parse(distill_body),
        orphan_notice=orphan_notice,
    )
    result = ingress_distill_llm(envelope, msg, config=config)
    out = emit_transition_simple_step_preserving_payload(
        msg,
        to_addr=FsmStage.ENRICH,
        from_stage=stage,
        settings=config,
    )
    score = ThreliumContentScoreWire.from_score(config.history.score_for(stage))
    for hp in result.parts:
        out.attach(
            _make_inline_text_part(
                EnrichContentId.from_history_body(hp.text),
                hp.text,
                score=score,
            )
        )
    return out


def _emit_to_cli_resume(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings,
) -> EmailMessage:
    msg = ingress_pipeline_email(msg)
    return build_fsm_step_to_stage(
        msg,
        to_addr=FsmStage.CLI_RESUME,
        from_stage=stage,
        system=extract_plain_body(msg).strip(),
        subject_line=_preserved_subject(msg),
        settings=config,
    )


def _preserved_subject(msg: EmailMessage) -> FsmTransitionPlainSubjectLine | None:
    """Сохранить исходный Subject входа (без ``Re:``-префикса билдера) для enrich-шаблона."""
    subj = RfcSubjectWire.parse_present_from_email(msg, MailHeaderName.SUBJECT)
    if subj is None:
        return None
    return FsmTransitionPlainSubjectLine.parse(subj.value)


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    require_unique_threading_rfc822_headers(msg)
    irt_wire = IngressRouterChildMsg.from_email(msg).in_reply_to
    if irt_wire is None:
        return _emit_to_enrich(msg, stage, config=config)

    with nm.open_parent_message_for_in_reply_to(irt_wire) as parent_msg:
        if parent_msg is None:
            log.info("irt_parent_not_found_orphan")
            return _emit_to_enrich(msg, stage, orphan=True, config=config)

        match classify_hitl_parent_notmuch(parent_msg):
            case HitlParentWithoutIntent():
                return _emit_to_enrich(msg, stage, config=config)
            case HitlParentWithIntent():
                return _emit_to_cli_resume(msg, stage, config=config)
