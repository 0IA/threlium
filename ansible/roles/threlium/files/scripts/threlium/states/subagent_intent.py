#!/usr/bin/env python3
"""subagent_intent@localhost → enrich@localhost; IRT-непрерывный переход с изолированным hop."""
from email.message import EmailMessage

from threlium.settings import ThreliumSettings
from threlium.e2e_directives import extract_e2e_int_directive
from threlium.fsm_emit import push_subagent_hop_budget
from threlium.fsm_emit_semantic import (
    emit_to_enrich,
    managed_patch_subagent_push_to_enrich,
)
from threlium.mime_reform import system_part_text
from threlium.prompts import render_prompt
from threlium.types import (
    EnrichCalleeHistoryText,
    EnrichRequestEchoText,
    EnrichUserQueryText,
    FsmStage,
    HopBudgetLine,
    PromptPath,
)


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    raw_task = system_part_text(msg)
    # E2E-ONLY: per-message override sub-бюджета (E2E_HOP_BUDGET_SUB:<int> в теле subagent-задачи) вместо
    # global threlium.yaml + рестарт engine (обобщение E2E_MID:, docs/E2E.md §2.3). Только за флагом e2e;
    # токен вырезается из задачи. В проде sub_override=None → лимит из settings.hop.budget_sub.
    sub_override: int | None = None
    if config.e2e.litellm_route_correlation:
        sub_override, raw_task = extract_e2e_int_directive(raw_task, "HOP_BUDGET_SUB")
    hb = push_subagent_hop_budget(
        HopBudgetLine.parse_from_email(msg), config, sub_max_override=sub_override
    )
    if hb is None:
        notice = render_prompt(PromptPath.SUBAGENT_INTENT_BUDGET_EXHAUSTED).strip()
        return emit_to_enrich(
            msg,
            stage,
            callee_history=EnrichCalleeHistoryText.parse(notice),
            settings=config,
        )
    task = EnrichUserQueryText.require_value(
        name="subagent task", raw=raw_task
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
