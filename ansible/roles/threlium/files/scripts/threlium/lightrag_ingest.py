"""Синтетический RFC822 для ``rag.ainsert``: shell из EmailMessage + Jinja только тело (ADR 0001)."""
from __future__ import annotations

from email import policy
from email.message import EmailMessage

from threlium.mime_reform import extract_plain_body
from threlium.prompts import render_prompt
from threlium.types import (
    MailHeaderName,
    PromptPath,
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


def _header_fold_one_line(raw: str | None) -> str:
    if raw is None:
        return ""
    return " ".join(str(raw).replace("\r\n", "\n").split())


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
        folded = _header_fold_one_line(wire.value)
        if not folded.strip():
            continue
        dst[name] = folded


def render_lightrag_ingest_document(msg: EmailMessage, *, thread_term: str) -> str:
    """RFC822-текст для ``rag.ainsert``: ``EmailMessage`` + ``policy.default``, тело из Jinja."""
    body_plain = extract_plain_body(msg)
    tt = thread_term.strip()
    subj_w = RfcSubjectWire.parse_present_from_email(msg, _HDR.SUBJECT)
    from_w = RfcFromWire.parse_present_from_email(msg, _HDR.FROM)
    subject_h = _header_fold_one_line(subj_w.value if subj_w is not None else None)
    from_h = _header_fold_one_line(from_w.value if from_w is not None else None)
    body_graph = render_prompt(
        PromptPath.LIGHTRAG_INGEST_BODY,
        body_plain=body_plain,
        thread_term=tt,
        subject_h=subject_h,
        from_h=from_h,
    )
    synthetic = EmailMessage()
    _copy_graph_headers(msg, synthetic)
    synthetic[_LRAG_HDR.THREAD_ID] = tt
    synthetic.set_content(
        body_graph.rstrip("\n"),
        subtype="plain",
        charset="utf-8",
    )
    return synthetic.as_string(policy=policy.default).strip() + "\n"
