"""response_append@localhost → enrich_fast@localhost.

Тело письма (content из tool call) уже сохранено в Maildir через fdm.
Простой forward в enrich_fast для быстрого цикла обратно в reasoning.
"""
from email.message import EmailMessage

from threlium.fsm_emit_semantic import emit_transition_simple_step_preserving_payload
from threlium.settings import ThreliumSettings
from threlium.types import FsmStage


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    return emit_transition_simple_step_preserving_payload(
        msg, to_addr=FsmStage.ENRICH_FAST, from_stage=stage, settings=config,
    )
