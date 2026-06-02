"""E2e wire RFC822 — фасад над :mod:`threlium.mail` (parse/serialize/SMTP)."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

from threlium.mail import parse_rfc822, serialize_rfc822_for_wire


def e2e_parse_rfc822(data: bytes) -> EmailMessage:
    return parse_rfc822(data)


def e2e_serialize_rfc822(msg: EmailMessage) -> bytes:
    return serialize_rfc822_for_wire(msg)


def e2e_smtp_send(
    host: str,
    port: int,
    msg: EmailMessage,
    *,
    timeout: float | None = None,
) -> None:
    """SMTP DATA с ``RFC822_FOR_INSERT`` (без fold 78 от ``send_message``)."""
    payload = serialize_rfc822_for_wire(msg)
    from_addr = parseaddr(msg.get("From", "pytest@localhost"))[1] or "pytest@localhost"
    to_hdrs: list[str] = []
    for h in ("To", "Cc", "Bcc"):
        to_hdrs.extend(msg.get_all(h, []))
    recipients = [a for _, a in getaddresses(to_hdrs) if a]
    if not recipients:
        raise ValueError("e2e_smtp_send: message has no To/Cc/Bcc recipients")
    smtp_kw: dict[str, float] = {}
    if timeout is not None:
        smtp_kw["timeout"] = timeout
    with smtplib.SMTP(host, port, **smtp_kw) as smtp:
        smtp.sendmail(from_addr, recipients, payload)
