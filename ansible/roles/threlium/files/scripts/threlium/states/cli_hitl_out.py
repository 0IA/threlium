#!/usr/bin/env python3
"""cli_hitl_out → egress_router@localhost: запрос подтверждения команды (ARCHITECTURE §6.2, §7)."""
import shlex
from email.message import EmailMessage

from threlium.cli_fsm import parse_cli_intent_payload
from threlium.fsm_emit import build_fsm_plain_to_stage
from threlium.mime_reform import extract_plain_body
from threlium.prompts import render_prompt
from threlium.settings import ThreliumSettings
from threlium.types import (
    FsmStage,
    FsmTransitionPlainBody,
    FsmTransitionPlainSubjectLine,
    PromptPath,
)


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    body = extract_plain_body(msg).strip()
    cli = parse_cli_intent_payload(body)
    if cli:
        line = " ".join(shlex.quote(a) for a in cli.argv)
        if cli.cwd:
            line = f"(cwd={shlex.quote(cli.cwd)}) {line}"
        user_body = render_prompt(PromptPath.CLI_HITL_OUT_CONFIRM, command_line=line)
    else:
        user_body = render_prompt(PromptPath.CLI_HITL_OUT_UNPARSABLE)
    return build_fsm_plain_to_stage(
        msg,
        to_addr=FsmStage.EGRESS_ROUTER,
        from_stage=stage,
        body=FsmTransitionPlainBody.parse(user_body),
        subject_line=FsmTransitionPlainSubjectLine.parse(
            render_prompt(PromptPath.CLI_HITL_OUT_SUBJECT).strip()
        ),
        settings=config,
    )
