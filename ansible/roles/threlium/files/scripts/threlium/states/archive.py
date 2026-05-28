"""archive@localhost: терминал хвоста отправки (тело собрано egress_* → ``archive``)."""
from __future__ import annotations

from email.message import EmailMessage

from threlium.settings import ThreliumSettings
from threlium.types import FsmStage


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    del msg, stage, config
    return None
