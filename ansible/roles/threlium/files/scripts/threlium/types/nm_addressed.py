"""Предикат ``EmailMessage`` по ``From:`` vs ``FsmStage`` (egress_router).

Предикаты на живом ``notmuch2.Message`` устранены: бизнес-логика стадий/HITL работает на иммутабельных
снимках (:meth:`threlium.types.notmuch_snapshot.IrtAncestorSnapshot.is_sent_from_fsm_stage` /
``is_addressed_to_fsm_stage``). Здесь остаётся только разбор адресов из stdlib ``EmailMessage``.
"""
from __future__ import annotations

from email.message import EmailMessage
from email.utils import getaddresses

from .fsm_stage import FsmStage
from threlium.mail_header_names import MailHeaderName


def email_message_sent_from_fsm_stage(msg: EmailMessage, stage: FsmStage) -> bool:
    """True, если ``From:`` stdlib ``EmailMessage`` содержит mailbox стадии."""
    raw = msg.get(MailHeaderName.FROM.value, "")
    if not raw or not raw.strip():
        return False
    want = stage.rfc822_mailbox.lower()
    for _, addr in getaddresses([raw]):
        if addr and addr.strip().lower() == want:
            return True
    return False
