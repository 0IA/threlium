"""Кастомный ``chunking_func`` для LightRAG: RFC822 ingest → чанки с префиксом заголовков."""
from __future__ import annotations

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import msgspec

from threlium.mime_reform import extract_plain_body
from threlium.types import (
    LightragChunkRecord,
    LightragWorkerBatchThreadIdKey,
    MailHeaderName,
    RfcFromWire,
    RfcInReplyToWire,
    RfcMessageIdWire,
    RfcSubjectWire,
    RfcToWire,
)
from threlium.types.lightrag_document_header import LightragDocumentHeader

_HDR = MailHeaderName
_LRAG_HDR = LightragDocumentHeader

_SUBJECT_PREFIX_MAX = 400


def _truncate_subject_wire(subj_w: RfcSubjectWire | None) -> str:
    s = (subj_w.value if subj_w is not None else "").strip()
    if len(s) <= _SUBJECT_PREFIX_MAX:
        return s
    return s[: _SUBJECT_PREFIX_MAX - 3] + "..."


def _chunk_prefix(em: EmailMessage, *, thread_val: str, chunk_number: int) -> str:
    """Префикс тела чанка: заголовки письма + ``X-Threlium-LightRAG-Chunk`` (1..N в рамках документа)."""
    from_w = RfcFromWire.parse_present_from_email(em, _HDR.FROM)
    to_w = RfcToWire.parse_present_from_email(em, _HDR.TO)
    subj_w = RfcSubjectWire.parse_present_from_email(em, _HDR.SUBJECT)
    mid_w = RfcMessageIdWire.parse_present_from_email(em, _HDR.MESSAGE_ID)
    irt_w = RfcInReplyToWire.parse_present_from_email(em, _HDR.IN_REPLY_TO)
    if chunk_number < 1:
        raise ValueError("threlium chunking: chunk_number must be >= 1")
    return "\n".join(
        [
            f"{_LRAG_HDR.THREAD_ID}: {thread_val}",
            f"{_HDR.FROM}: {from_w.value if from_w is not None else ''}",
            f"{_HDR.TO}: {to_w.value if to_w is not None else ''}",
            f"{_HDR.SUBJECT}: {_truncate_subject_wire(subj_w)}",
            f"{_LRAG_HDR.CHUNK_INDEX}: {chunk_number}",
            f"{_HDR.MESSAGE_ID}: {mid_w.value if mid_w is not None else ''}",
            f"{_HDR.IN_REPLY_TO}: {irt_w.value if irt_w is not None else ''}",
        ]
    )


def threlium_email_chunking_func(
    tokenizer,
    content: str,
    split_by_character: str | None,
    split_by_character_only: bool,
    chunk_overlap_token_size: int,
    chunk_token_size: int,
) -> list[dict[str, object]]:
    """Совместимо с сигнатурой LightRAG ``chunking_func`` (см. ``lightrag.lightrag``).

    ``split_by_character*`` игнорируются: границы задаются только разбором MIME и
    токенизацией **тела** (``chunk_token_size`` / ``chunk_overlap_token_size`` с
    инстанса ``LightRAG``).
    """
    del split_by_character, split_by_character_only
    raw = content.strip().encode("utf-8")
    em = BytesParser(policy=policy.default).parsebytes(raw)
    if not isinstance(em, EmailMessage):
        raise ValueError("threlium chunking: content is not a valid RFC822 message")

    tid_w = LightragWorkerBatchThreadIdKey.parse_present_from_email(em, _LRAG_HDR.THREAD_ID)
    thread_val = tid_w.value if tid_w is not None else ""
    if not thread_val:
        raise ValueError(f"threlium chunking: missing {_LRAG_HDR.THREAD_ID} header")

    plain = extract_plain_body(em).strip()
    body_tokens = tokenizer.encode(plain)
    body_max = max(32, int(chunk_token_size))
    overlap = max(0, min(body_max - 1, int(chunk_overlap_token_size)))
    step = max(1, body_max - overlap)

    results: list[dict[str, object]] = []
    if not body_tokens:
        chunk_text = _chunk_prefix(em, thread_val=thread_val, chunk_number=1) + "\n\n"
        results.append(
            msgspec.to_builtins(
                LightragChunkRecord(
                    tokens=len(tokenizer.encode(chunk_text)),
                    content=chunk_text.strip(),
                    chunk_order_index=0,
                )
            )
        )
        return results

    order = 0
    for start in range(0, len(body_tokens), step):
        window = body_tokens[start : start + body_max]
        piece = tokenizer.decode(window)
        chunk_no = order + 1
        prefix = _chunk_prefix(em, thread_val=thread_val, chunk_number=chunk_no)
        chunk_text = prefix + "\n\n" + piece
        results.append(
            msgspec.to_builtins(
                LightragChunkRecord(
                    tokens=len(tokenizer.encode(chunk_text)),
                    content=chunk_text.strip(),
                    chunk_order_index=order,
                )
            )
        )
        order += 1
        if start + body_max >= len(body_tokens):
            break
    return results
