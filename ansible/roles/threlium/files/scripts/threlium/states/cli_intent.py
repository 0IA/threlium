#!/usr/bin/env python3
"""cli_intent@localhost: детерминированная политика allow / deny / HITL (ARCHITECTURE §6.2).

allow  → cli_exec@localhost (новое письмо с канонизированным JSON).
deny   → ingress@localhost (отказ; далее enrich → reasoning).
hitl   → cli_hitl_out@localhost (подтверждение у пользователя).
"""
from email.message import EmailMessage

from threlium.cli_fsm import (
    classify_cli_policy,
    cli_payload_as_json,
    parse_cli_intent_payload,
)
from threlium.fsm_emit import build_fsm_plain_to_stage
from threlium.mime_reform import extract_plain_body
from threlium.prompts import render_prompt
from threlium.settings import ThreliumSettings
from threlium.types import (
    CliIntentPolicy,
    FsmStage,
    FsmTransitionPlainBody,
    FsmTransitionPlainSubjectLine,
    PromptPath,
)


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    body = extract_plain_body(msg).strip()
    payload = parse_cli_intent_payload(body)
    if not payload:
        return build_fsm_plain_to_stage(
            msg,
            to_addr=FsmStage.INGRESS,
            from_stage=stage,
            body=FsmTransitionPlainBody.parse(render_prompt(PromptPath.CLI_INTENT_INVALID)),
            subject_line=FsmTransitionPlainSubjectLine.parse(
                render_prompt(PromptPath.CLI_INTENT_INVALID_SUBJECT).strip()
            ),
            settings=config,
        )

    policy = classify_cli_policy(payload, config)
    canon = cli_payload_as_json(payload)

    if policy == CliIntentPolicy.DENY:
        return build_fsm_plain_to_stage(
            msg,
            to_addr=FsmStage.INGRESS,
            from_stage=stage,
            body=FsmTransitionPlainBody.parse(render_prompt(PromptPath.CLI_INTENT_DENIED)),
            subject_line=FsmTransitionPlainSubjectLine.parse(
                render_prompt(PromptPath.CLI_INTENT_DENIED_SUBJECT).strip()
            ),
            settings=config,
        )
    if policy == CliIntentPolicy.ALLOW:
        return build_fsm_plain_to_stage(
            msg, to_addr=FsmStage.CLI_EXEC, from_stage=stage, body=FsmTransitionPlainBody.parse(canon),
            settings=config,
        )
    return build_fsm_plain_to_stage(
        msg,
        to_addr=FsmStage.CLI_HITL_OUT,
        from_stage=stage,
        body=FsmTransitionPlainBody.parse(canon),
        settings=config,
    )
