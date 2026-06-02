"""response_append@localhost → enrich_fast@localhost.

Тело письма (content из tool call) уже сохранено в Maildir через fdm.
Простой forward в enrich_fast для быстрого цикла обратно в reasoning.
"""
from email.message import EmailMessage

from threlium.fsm_emit_semantic import emit_preserving_to_enrich_fast
from threlium.settings import ThreliumSettings
from threlium.types import FsmStage


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    return emit_preserving_to_enrich_fast(msg, stage, settings=config)
