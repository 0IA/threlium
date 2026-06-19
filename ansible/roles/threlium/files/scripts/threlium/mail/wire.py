"""Wire RFC822: parse/serialize/IMAP literal — единая политика stdlib ``email``."""
from __future__ import annotations

import glob
import imaplib
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Final

from threlium.types.identity import ImapFolderUid, imap_folder_uid_as_imaplib_arg

# Сериализация на wire (fdm, notmuch insert, msmtp): без refold длинных заголовков.
RFC822_FOR_INSERT: Final = policy.SMTP.clone(max_line_length=0, linesep="\n")

# Парсинг байтов: stdlib unfold при чтении; не compat32.
PARSE_RFC822: Final = policy.default.clone(max_line_length=0)


def parse_rfc822(data: bytes) -> EmailMessage:
    """Разбор байт → :class:`EmailMessage` (``PARSE_RFC822``)."""
    return BytesParser(policy=PARSE_RFC822).parsebytes(data)  # type: ignore[return-value]


def serialize_rfc822_for_wire(msg: EmailMessage) -> bytes:
    """Единственная сериализация письма на fdm / Maildir / SMTP wire."""
    return msg.as_bytes(policy=RFC822_FOR_INSERT)


def email_message_from_bytes(data: bytes) -> EmailMessage:
    """Граница «байты → полное письмо»."""
    return parse_rfc822(data)


def email_message_from_path(path: Path | str) -> EmailMessage:
    """``read_bytes`` + :func:`parse_rfc822`."""
    return parse_rfc822(Path(path).read_bytes())


def resolve_maildir_file(path: Path | str) -> Path | None:
    """Найти живой файл maildir-письма по неизменному base в ``{cur,new}``; ``None`` если файла нет.

    ``nm_settle`` (``unread``→seen) = атомарный notmuch-rename ``new/<base>`` → ``cur/<base>:2,S``:
    меняются только папка (``new``/``cur``) и суффикс ``:2,<flags>``, а base ``<time>.<unique>.<host>``
    неизменен. Поэтому замороженный в снимке путь может устареть между материализацией и чтением —
    резолвим живой файл по base. ``cur`` первой: к моменту чтения письмо обычно уже settled."""
    p = Path(path)
    base = p.name.split(":", 1)[0]
    maildir = p.parent.parent
    for sub in ("cur", "new"):
        for hit in glob.glob(glob.escape(str(maildir / sub / base)) + "*"):
            name = Path(hit).name
            # точное совпадение base: ``<base>`` (new) или ``<base>:2,<flags>`` (cur) — НЕ prefix
            # другого письма (``startswith(base)`` без разделителя — чужой файл).
            if name == base or name.startswith(base + ":"):
                return Path(hit)
    return None


def email_message_from_maildir(path: Path | str) -> EmailMessage:
    """:func:`email_message_from_path`, устойчивый к атомарному maildir-переносу ``new/``→``cur/``.

    Морозить путь для чтения нельзя (см. :func:`resolve_maildir_file`): читаем по переданному пути,
    пока он валиден (stat-fast-path — горячий путь без glob), иначе резолвим живой файл по base.
    Кэшировать следует РЕЗУЛЬТАТ (контент ``EmailMessage``), а не путь."""
    p = Path(path)
    target = p if p.exists() else resolve_maildir_file(p)
    if target is None:
        raise FileNotFoundError(
            f"maildir message gone: base={p.name.split(':', 1)[0]} dir={p.parent.parent}"
        )
    return email_message_from_path(target)


def canonicalize_mime(msg: EmailMessage) -> EmailMessage:
    """Round-trip MIME: serialize ``RFC822_FOR_INSERT`` → parse ``PARSE_RFC822``."""
    return BytesParser(policy=PARSE_RFC822).parsebytes(serialize_rfc822_for_wire(msg))  # type: ignore[return-value]


def _imap_body_fetch_parts(*, mark_seen: bool, headers_only: bool) -> str:
    """PART spec как у ``imap_tools.MailBox.fetch`` (BODY[.PEEK][HEADER] …)."""
    return (
        f"(BODY{'' if mark_seen else '.PEEK'}[{'HEADER' if headers_only else ''}] "
        "UID FLAGS RFC822.SIZE)"
    )


def _extract_imap_fetch_literal(data: list) -> bytes:
    """Литерал BODY[] из ответа ``imaplib`` — последний непустой ``tuple[1]`` (как imap_tools)."""
    literal: bytes | None = None
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and item[1]:
            literal = item[1]
    return literal or b""


def imap_fetch_rfc822_bytes(
    client: imaplib.IMAP4,
    uid: ImapFolderUid,
    *,
    mark_seen: bool = False,
    headers_only: bool = False,
) -> bytes:
    """UID FETCH → оригинальные байты с сервера (без ``imap_tools.MailMessage``)."""
    uid_arg = imap_folder_uid_as_imaplib_arg(uid)
    parts = _imap_body_fetch_parts(mark_seen=mark_seen, headers_only=headers_only)
    typ, data = client.uid("fetch", uid_arg, parts)
    if typ != "OK" or not data or data[0] is None:
        raise RuntimeError(f"IMAP: UID FETCH uid={uid_arg} → {typ!r} {data!r}")
    raw = _extract_imap_fetch_literal(data)
    if not raw:
        raise RuntimeError(f"IMAP: UID FETCH uid={uid_arg} без literal BODY")
    return raw
