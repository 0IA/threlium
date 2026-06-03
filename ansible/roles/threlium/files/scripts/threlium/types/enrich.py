"""Проекции контекста стадии ``enrich`` для Jinja (``docs/TYPES.md`` уровень 3).

Граница: ``EmailMessage`` → :class:`EnrichQueryPlanThreadSkeletonEntry` /
:class:`EnrichQueryPlanRecentMessageEntry`; в ``render_prompt`` уходят только
развёрнутые ``str`` (как :class:`~threlium.types.reasoning.ReasoningIncomingEnvelope`
в ``states/reasoning.py``).
"""
from __future__ import annotations

from email.message import EmailMessage
from typing import Self

import msgspec

from threlium.mail_header_names import MailHeaderName
from threlium.mime_reform import concat_history_parts_text
from threlium.types.rfc import RfcDateWire, RfcFromWire, RfcSubjectWire, RfcToWire

_HDR = MailHeaderName


def _present_optional_wire(
    wire: RfcDateWire | RfcFromWire | RfcSubjectWire | RfcToWire | None,
) -> str:
    return wire.value if wire is not None else ""


class EnrichQueryPlanThreadSkeletonEntry(msgspec.Struct, frozen=True, kw_only=True):
    """Одна строка таймлайна старых писем для ``lightrag/enrich_query_plan.j2``."""

    date: RfcDateWire | None
    from_hdr: RfcFromWire | None
    subject: RfcSubjectWire | None

    @classmethod
    def from_email(cls, msg: EmailMessage) -> Self:
        return cls(
            date=RfcDateWire.parse_present_from_email(msg, _HDR.DATE),
            from_hdr=RfcFromWire.parse_present_from_email(msg, _HDR.FROM),
            subject=RfcSubjectWire.parse_present_from_email(msg, _HDR.SUBJECT),
        )

    def for_query_plan_jinja(self) -> dict[str, str]:
        return {
            "date": _present_optional_wire(self.date),
            "from_hdr": _present_optional_wire(self.from_hdr),
            "subject": _present_optional_wire(self.subject),
        }


class EnrichQueryPlanRecentMessageEntry(msgspec.Struct, frozen=True, kw_only=True):
    """Недавнее письмо unified-контекста для ``lightrag/enrich_query_plan.j2``."""

    from_hdr: RfcFromWire | None
    to_hdr: RfcToWire | None
    date: RfcDateWire | None
    subject: RfcSubjectWire | None
    history_text: str

    @classmethod
    def from_email(cls, msg: EmailMessage) -> Self:
        return cls(
            from_hdr=RfcFromWire.parse_present_from_email(msg, _HDR.FROM),
            to_hdr=RfcToWire.parse_present_from_email(msg, _HDR.TO),
            date=RfcDateWire.parse_present_from_email(msg, _HDR.DATE),
            subject=RfcSubjectWire.parse_present_from_email(msg, _HDR.SUBJECT),
            history_text=concat_history_parts_text(msg),
        )

    def for_query_plan_jinja(self) -> dict[str, str]:
        return {
            "from_hdr": _present_optional_wire(self.from_hdr),
            "to_hdr": _present_optional_wire(self.to_hdr),
            "date": _present_optional_wire(self.date),
            "subject": _present_optional_wire(self.subject),
            "history_text": self.history_text,
        }


__all__ = [
    "EnrichQueryPlanRecentMessageEntry",
    "EnrichQueryPlanThreadSkeletonEntry",
]
