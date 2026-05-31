#!/usr/bin/env python3
"""cli_intent@localhost: sandbox / privileged (+ optional HITL) → cli_exec или cli_hitl_out.

route-collision → enrich_fast@localhost (observation-note: имя маршрута — tool, не CLI).
SANDBOX → cli_exec@localhost (user scope + systemd sandbox).
PRIVILEGED + privileged_hitl_enabled → cli_hitl_out → cli_resume → cli_exec (uid=0).
PRIVILEGED без HITL → cli_exec сразу (system scope).
invalid payload → enrich_fast@localhost.
"""
from email.message import EmailMessage

from threlium.cli_fsm import (
    classify_cli_intent,
    cli_payload_as_json,
    parse_cli_intent_payload,
)
from threlium.fsm_emit import build_fsm_plain_to_stage, build_fsm_step_to_stage
from threlium.mime_reform import system_part_text
from threlium.prompts import render_prompt
from threlium.settings import ThreliumSettings
from threlium.types import (
    CliExecDecision,
    CliIntentPolicy,
    CliRouteCollision,
    FsmStage,
    FsmTransitionPlainBody,
    FsmTransitionPlainSubjectLine,
    PromptPath,
)


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    body = system_part_text(msg).strip()
    payload = parse_cli_intent_payload(body)
    if not payload:
        return build_fsm_step_to_stage(
            msg,
            to_addr=FsmStage.ENRICH_FAST,
            from_stage=stage,
            history=render_prompt(PromptPath.CLI_INTENT_INVALID, prior=body).strip(),
            settings=config,
        )

    canon = cli_payload_as_json(payload)

    match classify_cli_intent(payload):
        case CliRouteCollision(route=route, cmd=cmd):
            note = render_prompt(
                PromptPath.CLI_INTENT_ROUTE_COLLISION, route=route.value, cmd=cmd
            ).strip()
            return build_fsm_step_to_stage(
                msg,
                to_addr=FsmStage.ENRICH_FAST,
                from_stage=stage,
                history=note,
                settings=config,
            )
        case CliExecDecision(policy=CliIntentPolicy.SANDBOX):
            return build_fsm_plain_to_stage(
                msg,
                to_addr=FsmStage.CLI_EXEC,
                from_stage=stage,
                body=FsmTransitionPlainBody.parse(canon),
                settings=config,
            )
        case CliExecDecision(policy=CliIntentPolicy.PRIVILEGED):
            if config.cli.privileged_hitl_enabled:
                return build_fsm_plain_to_stage(
                    msg,
                    to_addr=FsmStage.CLI_HITL_OUT,
                    from_stage=stage,
                    body=FsmTransitionPlainBody.parse(canon),
                    settings=config,
                )
            return build_fsm_plain_to_stage(
                msg,
                to_addr=FsmStage.CLI_EXEC,
                from_stage=stage,
                body=FsmTransitionPlainBody.parse(canon),
                settings=config,
            )
