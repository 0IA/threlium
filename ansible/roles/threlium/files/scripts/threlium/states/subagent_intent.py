#!/usr/bin/env python3
"""subagent_intent@localhost → enrich@localhost; IRT-непрерывный переход с изолированным hop."""
from email.message import EmailMessage

from threlium.settings import ThreliumSettings
from threlium.fsm_emit import push_subagent_hop_budget
from threlium.fsm_emit_semantic import (
    emit_to_enrich,
    managed_patch_subagent_push_to_enrich,
)
from threlium.mime_reform import system_part_text
from threlium.prompts import render_prompt
from threlium.types import (
    EnrichRequestEchoText,
    EnrichUserQueryText,
    FsmStage,
    HopBudgetLine,
    PromptPath,
)


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    hb = push_subagent_hop_budget(HopBudgetLine.parse_from_email(msg), config)
    if hb is None:
        notice = render_prompt(PromptPath.SUBAGENT_INTENT_BUDGET_EXHAUSTED).strip()
        user_query = EnrichUserQueryText.require(name="subagent budget notice", raw=notice)
        return emit_to_enrich(
            msg,
            stage,
            user_query=user_query,
            settings=config,
        )
    task = EnrichUserQueryText.require_value(
        name="subagent task", raw=system_part_text(msg)
    )
    return emit_to_enrich(
        msg,
        stage,
        user_query=task,
        request_echo=EnrichRequestEchoText.parse(task.value),
        settings=config,
        managed_headers=managed_patch_subagent_push_to_enrich(
            msg,
            hop_budget=hb,
        ),
    )
