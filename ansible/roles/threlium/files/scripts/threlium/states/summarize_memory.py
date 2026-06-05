#!/usr/bin/env python3
"""summarize_memory@localhost: стадия-хранитель итога суммаризации → enrich@."""
from __future__ import annotations

from email.message import EmailMessage

from threlium.fsm_emit_semantic import emit_reenrich_to_enrich
from threlium.settings import ThreliumSettings
from threlium.types import FsmStage


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    return emit_reenrich_to_enrich(
        msg,
        stage,
        relay_history_from=msg,
        settings=config,
    )
