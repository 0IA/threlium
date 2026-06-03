#!/usr/bin/env python3
"""reflect@localhost → enrich@localhost (docs/MEMORY_TABLE.md §3).

Re-enrich без ingress: callee рендерит reflect body в ``<user-query>`` (local turn).
"""
from __future__ import annotations

import re
from email.message import EmailMessage

from threlium.fsm_emit_semantic import emit_to_enrich
from threlium.logutil import logger
from threlium.mime_reform import system_part_text
from threlium.prompts import render_prompt
from threlium.settings import ThreliumSettings
from threlium.types import (
    EnrichUserQueryText,
    FsmStage,
    HopBudgetLine,
    MailHeaderName,
    PromptPath,
    RfcSubjectWire,
)

from threlium.fsm_emit import HDR_HOP_BUDGET

_HDR = MailHeaderName

log = logger.bind(stage="reflect")

CYCLE_COST = 3
SAFETY_MARGIN = 1


def _remaining_hops(line: HopBudgetLine) -> int:
    s = line.value
    if not s:
        return 0
    last = s.split()[-1]
    if re.fullmatch(r"\d+", last):
        return int(last)
    m = re.fullmatch(r"(\d+)-(\d+)", last)
    if not m:
        return 0
    return max(0, int(m.group(1)) - int(m.group(2)))


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    raw_budget = HopBudgetLine.parse_present_from_email(msg, HDR_HOP_BUDGET)
    line = raw_budget or HopBudgetLine.parse(None)
    remaining = _remaining_hops(line)
    log.info("reflect_to_enrich", remaining_hops=remaining)

    subj_w = RfcSubjectWire.parse_present_from_email(msg, _HDR.SUBJECT)
    subject = subj_w.value if subj_w is not None else None
    previous_reasoning = system_part_text(msg).strip()
    template = (
        PromptPath.REFLECT_CONTINUE
        if remaining >= CYCLE_COST + SAFETY_MARGIN
        else PromptPath.REFLECT_FINAL
    )
    body = render_prompt(
        template,
        subject=subject,
        previous_reasoning=previous_reasoning,
        remaining_hops=remaining,
        cycle_cost=CYCLE_COST,
    ).strip()
    user_query = EnrichUserQueryText.require(name="reflect body", raw=body)
    return emit_to_enrich(
        msg,
        stage,
        user_query=user_query,
        settings=config,
    )
