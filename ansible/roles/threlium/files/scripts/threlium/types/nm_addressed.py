"""Предикаты ``notmuch2.Message`` и ``EmailMessage`` по ``To:`` и ``From:`` vs ``FsmStage``.

Единая граница разбора адресных заголовков для HITL-детекции,
ancestor-walk cli_intent и egress_router (``docs/TYPES.md``).
"""
from __future__ import annotations

from email.message import EmailMessage
from email.utils import getaddresses

import notmuch2  # pyright: ignore[reportMissingImports]

from .fsm_stage import FsmStage
from threlium.mail_header_names import MailHeaderName


def notmuch_message_addressed_to_fsm_stage(nm_msg: notmuch2.Message, stage: FsmStage) -> bool:
    """True, если среди адресатов ``To:`` есть ровно канонический mailbox стадии (регистронезависимо)."""
    try:
        raw = str(nm_msg.header(MailHeaderName.TO.value))
    except (LookupError, notmuch2.NullPointerError):
        return False
    if not raw.strip():
        return False
    want = stage.rfc822_mailbox.lower()
    for _, addr in getaddresses([raw]):
        if addr and addr.strip().lower() == want:
            return True
    return False


def notmuch_message_sent_from_fsm_stage(nm_msg: notmuch2.Message, stage: FsmStage) -> bool:
    """True, если ``From:`` содержит канонический mailbox стадии (регистронезависимо)."""
    try:
        raw = str(nm_msg.header(MailHeaderName.FROM.value))
    except (LookupError, notmuch2.NullPointerError):
        return False
    if not raw.strip():
        return False
    want = stage.rfc822_mailbox.lower()
    for _, addr in getaddresses([raw]):
        if addr and addr.strip().lower() == want:
            return True
    return False


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
