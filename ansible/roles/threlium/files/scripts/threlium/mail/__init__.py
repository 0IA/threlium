"""Публичный wire-контракт RFC822: parse, serialize, IMAP literal.

Enrich/MIME-части — :mod:`threlium.mime_reform`. Заголовки (смысл) — :mod:`threlium.types`.
"""
from threlium.mail.wire import (
    PARSE_RFC822,
    RFC822_FOR_INSERT,
    canonicalize_mime,
    email_message_from_bytes,
    email_message_from_maildir,
    email_message_from_path,
    imap_fetch_rfc822_bytes,
    parse_rfc822,
    resolve_maildir_file,
    serialize_rfc822_for_wire,
)

__all__ = [
    "PARSE_RFC822",
    "RFC822_FOR_INSERT",
    "canonicalize_mime",
    "email_message_from_bytes",
    "email_message_from_maildir",
    "email_message_from_path",
    "imap_fetch_rfc822_bytes",
    "parse_rfc822",
    "resolve_maildir_file",
    "serialize_rfc822_for_wire",
]
