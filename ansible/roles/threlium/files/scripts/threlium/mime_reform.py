"""Разбор/сборка MIME для мостов и стадий — поверх stdlib ``email``."""
from __future__ import annotations

from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeAlias

from threlium.logutil import logger
from threlium.mail_header_names import MailHeaderName

if TYPE_CHECKING:
    from threlium.types.fsm_strings import (
        EnrichGlobalMemoryText,
        EnrichGraphAnswerText,
        EnrichThreadMemoryText,
        EnrichUnifiedMailContextText,
    )

    _EnrichOptionalText: TypeAlias = (
        EnrichGraphAnswerText
        | EnrichUnifiedMailContextText
        | EnrichThreadMemoryText
        | EnrichGlobalMemoryText
        | None
    )

log = logger.bind(stage="mime_reform")


class EnrichPartId(StrEnum):
    """Content-ID для MIME-частей письма enrich/enrich_fast -> reasoning."""

    USER_MESSAGE = "<user-message>"
    GRAPH_ANSWER = "<graph-answer>"
    UNIFIED_MAIL_CONTEXT = "<unified-mail-context>"
    THREAD_MEMORY = "<thread-memory>"
    GLOBAL_MEMORY = "<global-memory>"
    RESPONSE_STATE = "<response-state>"
    PLAN_STATE = "<plan-state>"
    MEMORY_NOTE = "<memory-note>"

_HDR = MailHeaderName

# Единая политика сериализации RFC822 для приложения: ``email.policy.SMTP`` с
# ``max_line_length=0`` (без refold длинных заголовков) и ``linesep`` как Unix LF.
# Используется для fdm stdin / ``notmuch insert``, handoff движка, egress msmtp prep,
# round-trip в :func:`canonicalize_mime` (см. docs/INDEX.md §4 — ранее ``reformail -c``).
RFC822_FOR_INSERT: Final = policy.SMTP.clone(max_line_length=0, linesep="\n")

_PARSE_RFC822: Final = policy.default.clone(max_line_length=0)


def _extract_plain_body_from_message(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                raw = part.get_payload(decode=True)
                if isinstance(raw, bytes):
                    return raw.decode(part.get_content_charset() or "utf-8", errors="replace")
                return "" if raw is None else str(raw)
        return ""
    raw = msg.get_payload(decode=True)
    if isinstance(raw, bytes):
        return raw.decode(msg.get_content_charset() or "utf-8", errors="replace")
    if raw is None:
        pl = msg.get_payload()
        return "" if pl is None else str(pl)
    return str(raw)


def extract_plain_body(msg: EmailMessage) -> str:
    """Текстовое тело EmailMessage: первый text/plain, иначе raw payload."""
    return _extract_plain_body_from_message(msg)


def ingress_raw_email_capture(incoming: EmailMessage) -> str:
    """Все заголовки входящего MIME + пустая строка + только ``text/plain`` тело (как :func:`extract_plain_body`)."""
    lines: list[str] = []
    for key, val in incoming.items():
        lines.append(f"{key}: {val}")
    lines.append("")
    lines.append(extract_plain_body(incoming))
    return "\n".join(lines)


def require_unique_threading_rfc822_headers(msg: EmailMessage) -> None:
    """Fail-fast на входе ``ingress``: более одного физического ``In-Reply-To`` или ``References``.

    Дубликаты недопустимы после канонизации email-моста: ``EmailMessage`` может накапливать
    несколько одноимённых заголовков; ``get()`` и notmuch parent lookup опираются на первый —
    ломается инвариант треда (см. ``bridges.email._build_canonical`` skip IRT/Refs).
    """
    for hdr in (_HDR.IN_REPLY_TO, _HDR.REFERENCES):
        vals = msg.get_all(hdr)
        n = len(vals) if vals else 0
        if n > 1:
            raise RuntimeError(
                "FSM-инвариант: ожидается не более одного заголовка "
                f"{hdr!r}, получено {n}. Дубли In-Reply-To/References ломают "
                "поиск родителя в notmuch; проверьте email-мост и RFC822 на диске."
            )


def ingress_pipeline_email(incoming: EmailMessage) -> EmailMessage:
    """Письмо для handoff после ingress: моночасть ``text/plain``, без multipart/вложений.

    Заголовки переносятся как в orphan-префиксе (:mod:`threlium.states.ingress`), тело —
    :func:`extract_plain_body`.
    """
    out = EmailMessage()
    skip = frozenset(
        {
            _HDR.CONTENT_TYPE.value.lower(),
            _HDR.CONTENT_TRANSFER_ENCODING.value.lower(),
            _HDR.MIME_VERSION.value.lower(),
            _HDR.CONTENT_DISPOSITION.value.lower(),
        }
    )
    for k, v in incoming.items():
        if k.lower() in skip:
            continue
        if k in out:
            out.add_header(k, v)
        else:
            out[k] = v
    body = extract_plain_body(incoming)
    out.set_content(body, subtype="plain", charset="utf-8")
    return out


def _make_inline_text_part(content_id: EnrichPartId, text: str) -> EmailMessage:
    """MIME text/plain part с Content-ID и Content-Disposition: inline."""
    part = EmailMessage()
    part.set_content(text, subtype="plain", charset="utf-8")
    part.add_header("Content-Disposition", "inline")
    part.replace_header("Content-Type", "text/plain; charset=\"utf-8\"")
    part["Content-ID"] = content_id.value
    return part


def _copy_envelope_headers(src: EmailMessage, dst: EmailMessage) -> None:
    """Скопировать заголовки из src в dst, пропуская MIME-структурные."""
    skip = frozenset(
        {
            _HDR.CONTENT_TYPE.value.lower(),
            _HDR.CONTENT_TRANSFER_ENCODING.value.lower(),
            _HDR.MIME_VERSION.value.lower(),
            _HDR.CONTENT_DISPOSITION.value.lower(),
        }
    )
    for k, v in src.items():
        if k.lower() in skip:
            continue
        if k in dst:
            dst.add_header(k, v)
        else:
            dst[k] = v


def build_enriched_multipart(
    incoming: EmailMessage,
    *,
    user_message_text: str,
    graph_answer: EnrichGraphAnswerText | None,
    unified_mail_context: EnrichUnifiedMailContextText | None,
    thread_memory: EnrichThreadMemoryText | None,
    global_memory: EnrichGlobalMemoryText | None,
    stage: str,
    extra_parts: list[tuple[EnrichPartId, str]] | None = None,
) -> EmailMessage:
    """``multipart/mixed`` с MIME-частями по ``Content-ID`` (RFC 2045/2046).

    Каждый смысловой блок — отдельная ``text/plain`` part с
    ``Content-Disposition: inline`` и уникальным ``Content-ID``.
    """
    container = EmailMessage()
    container.make_mixed()
    _copy_envelope_headers(incoming, container)

    container.attach(
        _make_inline_text_part(EnrichPartId.USER_MESSAGE, user_message_text.strip())
    )

    _VO_PARTS: list[tuple[EnrichPartId, _EnrichOptionalText]] = [
        (EnrichPartId.GRAPH_ANSWER, graph_answer),
        (EnrichPartId.UNIFIED_MAIL_CONTEXT, unified_mail_context),
        (EnrichPartId.THREAD_MEMORY, thread_memory),
        (EnrichPartId.GLOBAL_MEMORY, global_memory),
    ]
    part_ids = [EnrichPartId.USER_MESSAGE.value]
    for pid, vo in _VO_PARTS:
        if vo is not None and vo.value:
            container.attach(_make_inline_text_part(pid, vo.value))
            part_ids.append(pid.value)

    if extra_parts:
        for pid, text in extra_parts:
            container.attach(_make_inline_text_part(pid, text))
            part_ids.append(pid.value)

    logger.bind(stage=stage).info("built_enriched_multipart", parts=part_ids)
    return container


def extract_part_by_content_id(msg: EmailMessage, content_id: EnrichPartId) -> str | None:
    """Текст MIME-part с заданным ``Content-ID``, или ``None``."""
    if not msg.is_multipart():
        return None
    for part in msg.walk():
        if part.is_multipart():
            continue
        if part.get("Content-ID") == content_id.value:
            raw = part.get_payload(decode=True)
            if isinstance(raw, bytes):
                return raw.decode(part.get_content_charset() or "utf-8", errors="replace")
            return None if raw is None else str(raw)
    return None


def replace_or_add_part(
    msg: EmailMessage,
    content_id: EnrichPartId,
    new_text: str,
) -> EmailMessage:
    """Заменить MIME-part с ``Content-ID`` или добавить новую.

    Возвращает новый ``EmailMessage`` (multipart пересобирается).
    """
    out = EmailMessage()
    out.make_mixed()
    _copy_envelope_headers(msg, out)

    replaced = False
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            if part.get("Content-ID") == content_id.value:
                out.attach(_make_inline_text_part(content_id, new_text))
                replaced = True
            else:
                out.attach(part)

    if not replaced:
        out.attach(_make_inline_text_part(content_id, new_text))

    return out


def canonicalize_mime(msg: EmailMessage) -> EmailMessage:
    """Round-trip MIME средствами stdlib ``email``.

    Сериализация :data:`RFC822_FOR_INSERT` (Unix LF, без refold длинных строк) →
    парсинг ``policy.default``. Заменяет прежний
    ``reformime -r -s0``: без subprocess, без внешних бинарников.

    Эквивалентно типичному use-case на наших данных, где сообщение уже
    распарсено либо воркером (``parse_rfc822``), либо ``BytesParser(default)``
    (мост ``bridges.email`` — long-running IMAP IDLE bridge).
    """
    return BytesParser(policy=policy.default).parsebytes(
        msg.as_bytes(policy=RFC822_FOR_INSERT)
    )  # type: ignore[return-value]


def parse_rfc822(data: bytes) -> EmailMessage:
    """Разбор байт → EmailMessage (policy.default + ``max_line_length=0`` на парсе)."""
    return BytesParser(policy=_PARSE_RFC822).parsebytes(data)  # type: ignore[return-value]


def email_message_from_bytes(data: bytes) -> EmailMessage:
    """Алиас :func:`parse_rfc822` для явной границы «байты → полное письмо»."""
    return parse_rfc822(data)


def email_message_from_path(path: Path | str) -> EmailMessage:
    """Один проход ``read_bytes`` + :func:`parse_rfc822` (runner'ы, pending)."""
    return parse_rfc822(Path(path).read_bytes())


