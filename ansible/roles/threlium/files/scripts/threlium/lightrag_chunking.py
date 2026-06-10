"""Кастомный ``chunking_func`` для LightRAG: RFC822 ingest → чанки с префиксом заголовков."""
from __future__ import annotations

from email.message import EmailMessage

import msgspec

from threlium.mail import parse_rfc822
from threlium.mime_reform import history_part_text, iter_history_parts
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


def _document_prefix_parts(em: EmailMessage, *, thread_val: str) -> tuple[str, str]:
    """Инвариантные на ВЕСЬ документ части префикса чанка (всё, кроме ``X-Threlium-LightRAG-Chunk: N``).

    Заголовки письма (From/To/Subject/Message-ID/In-Reply-To) одинаковы для всех чанков документа,
    поэтому разбираются в свои VO (``Rfc*Wire.parse_present_from_email`` — граница письма, ``docs/TYPES.md``
    §68/§72) РОВНО ОДИН раз на документ, а не на каждый чанк: py-spy ловил ``_chunk_prefix``→
    ``parse_present_from_email`` как горячую GIL-точку rag-loop'а (5 разборов × N чанков). Возвращает
    ``(head, tail)`` — строки до и после строки ``CHUNK_INDEX``, которую вставляет :func:`_chunk_prefix`.
    """
    from_w = RfcFromWire.parse_present_from_email(em, _HDR.FROM)
    to_w = RfcToWire.parse_present_from_email(em, _HDR.TO)
    subj_w = RfcSubjectWire.parse_present_from_email(em, _HDR.SUBJECT)
    mid_w = RfcMessageIdWire.parse_present_from_email(em, _HDR.MESSAGE_ID)
    irt_w = RfcInReplyToWire.parse_present_from_email(em, _HDR.IN_REPLY_TO)
    head = "\n".join(
        [
            f"{_LRAG_HDR.THREAD_ID}: {thread_val}",
            f"{_HDR.FROM}: {from_w.value if from_w is not None else ''}",
            f"{_HDR.TO}: {to_w.value if to_w is not None else ''}",
            f"{_HDR.SUBJECT}: {_truncate_subject_wire(subj_w)}",
        ]
    )
    tail = "\n".join(
        [
            f"{_HDR.MESSAGE_ID}: {mid_w.value if mid_w is not None else ''}",
            f"{_HDR.IN_REPLY_TO}: {irt_w.value if irt_w is not None else ''}",
        ]
    )
    return head, tail


def _chunk_prefix(prefix_head: str, prefix_tail: str, chunk_number: int) -> str:
    """Префикс тела чанка из заранее разобранных частей документа + ``X-Threlium-LightRAG-Chunk`` (1..N)."""
    if chunk_number < 1:
        raise ValueError("threlium chunking: chunk_number must be >= 1")
    return f"{prefix_head}\n{_LRAG_HDR.CHUNK_INDEX}: {chunk_number}\n{prefix_tail}"


def threlium_email_chunking_func(
    tokenizer,
    content: str,
    split_by_character: str | None,
    split_by_character_only: bool,
    chunk_overlap_token_size: int,
    chunk_token_size: int,
) -> list[dict[str, object]]:
    """Совместимо с сигнатурой LightRAG ``chunking_func`` (см. ``lightrag.lightrag``).

    ``split_by_character*`` игнорируются: границы задаются разбором MIME и токенизацией.
    Чанкинг идёт **по отдельным** ``<history>``-частям synthetic ingest-письма
    (``docs/CONTEXT_CONTRACT.md`` §7), без слияния в одно plain-тело: малая часть
    (``tokens <= chunk_token_size``) → один чанк, большая → окно/overlap внутри части.
    Нумерация ``chunk_order_index`` сквозная 1..N по всему документу.
    """
    del split_by_character, split_by_character_only
    raw = content.strip().encode("utf-8")
    em = parse_rfc822(raw)
    if not isinstance(em, EmailMessage):
        raise ValueError("threlium chunking: content is not a valid RFC822 message")

    tid_w = LightragWorkerBatchThreadIdKey.parse_present_from_email(em, _LRAG_HDR.THREAD_ID)
    thread_val = tid_w.value if tid_w is not None else ""
    if not thread_val:
        raise ValueError(f"threlium chunking: missing {_LRAG_HDR.THREAD_ID} header")

    # Заголовки документа — РОВНО ОДИН раз (не на каждый чанк): см. _document_prefix_parts. Префиксный
    # токен-overhead тоже считаем один раз; chunk_token = prefix-overhead + длина окна тела (тело уже
    # токенизировано в body_tokens — не ре-токенизируем чанк целиком на каждое окно). Точное число не
    # требуется (это метаданные; эмбеддинги в e2e константны, прод-ветки нет).
    prefix_head, prefix_tail = _document_prefix_parts(em, thread_val=thread_val)
    prefix_overhead_tokens = len(tokenizer.encode(_chunk_prefix(prefix_head, prefix_tail, 1) + "\n\n"))

    body_max = max(32, int(chunk_token_size))
    overlap = max(0, min(body_max - 1, int(chunk_overlap_token_size)))
    step = max(1, body_max - overlap)

    results: list[dict[str, object]] = []
    order = 0
    for _cid, part in iter_history_parts(em):
        plain = history_part_text(part).strip()
        if not plain:
            continue
        body_tokens = tokenizer.encode(plain)
        if not body_tokens:
            continue
        if len(body_tokens) <= body_max:
            windows: list[list[int]] = [body_tokens]
        else:
            windows = []
            for start in range(0, len(body_tokens), step):
                windows.append(body_tokens[start : start + body_max])
                if start + body_max >= len(body_tokens):
                    break
        for window in windows:
            piece = tokenizer.decode(window)
            prefix = _chunk_prefix(prefix_head, prefix_tail, order + 1)
            chunk_text = prefix + "\n\n" + piece
            results.append(
                msgspec.to_builtins(
                    LightragChunkRecord(
                        tokens=prefix_overhead_tokens + len(window),
                        content=chunk_text.strip(),
                        chunk_order_index=order,
                    )
                )
            )
            order += 1

    if not results:
        raise ValueError(
            "threlium chunking: ingest-документ без непустых <history>-частей "
            "(drain-gate message_has_history гарантирует их наличие — это нарушение инварианта)"
        )
    return results
