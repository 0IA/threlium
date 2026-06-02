"""Синтетический RFC822 для ``rag.ainsert``: envelope из EmailMessage + N ``<history>``-частей.

Контракт ``docs/CONTEXT_CONTRACT.md`` §7: ingest **не сливает** ``<history>`` в одно
plain-тело — каждая history-часть переезжает в synthetic ``multipart/mixed`` как inline
``text/plain`` с тем же контент-адресным CID ``<{sha256(body)}@history>``, что и на диске.
Границы distill-частей сохраняются для per-part chunking (``lightrag_chunking``).
"""
from __future__ import annotations

from email.message import EmailMessage

from threlium.mail import serialize_rfc822_for_wire
from threlium.mime_reform import (
    EnrichContentId,
    _make_inline_text_part,
    history_part_text,
    iter_history_parts,
)
from threlium.types import (
    MailHeaderName,
    RfcDateWire,
    RfcFromWire,
    RfcInReplyToWire,
    RfcMessageIdWire,
    RfcReferencesWire,
    RfcSubjectWire,
    RfcToWire,
)
from threlium.types.lightrag_document_header import LightragDocumentHeader

_HDR = MailHeaderName
_LRAG_HDR = LightragDocumentHeader


def _copy_graph_headers(src: EmailMessage, dst: EmailMessage) -> None:
    """Копирование выбранных заголовков через ``Rfc*Wire.parse_present_from_email`` (``docs/TYPES.md``)."""
    parsers: list[tuple[str, type]] = [
        (_HDR.FROM, RfcFromWire),
        (_HDR.TO, RfcToWire),
        (_HDR.SUBJECT, RfcSubjectWire),
        (_HDR.DATE, RfcDateWire),
        (_HDR.MESSAGE_ID, RfcMessageIdWire),
        (_HDR.IN_REPLY_TO, RfcInReplyToWire),
        (_HDR.REFERENCES, RfcReferencesWire),
    ]
    for name, wire_cls in parsers:
        wire = wire_cls.parse_present_from_email(src, name)
        if wire is None:
            continue
        if not wire.value.strip():
            continue
        dst[name] = wire.value


def render_lightrag_ingest_document(msg: EmailMessage, *, thread_term: str) -> str:
    """RFC822-текст для ``rag.ainsert``: envelope-заголовки + по одной inline-части на ``<history>``.

    Порядок частей = порядок :func:`iter_history_parts`; пустые тела пропускаются. CID каждой
    части пересчитывается из тела (:meth:`EnrichContentId.from_history_body`) — совпадает с
    диском, если тело идентично. Слияния в одно plain-тело больше нет.
    """
    tt = thread_term.strip()
    synthetic = EmailMessage()
    synthetic.make_mixed()
    _copy_graph_headers(msg, synthetic)
    synthetic[_LRAG_HDR.THREAD_ID] = tt
    for _cid, part in iter_history_parts(msg):
        text = history_part_text(part).strip()
        if not text:
            continue
        synthetic.attach(
            _make_inline_text_part(EnrichContentId.from_history_body(text), text)
        )
    return serialize_rfc822_for_wire(synthetic).decode("utf-8", errors="replace").strip() + "\n"
