#!/usr/bin/env python3
"""Email-bridge: IMAP IDLE → canonicalize → fdm (long-running).

Единый long-running процесс: подключение к IMAP, IDLE-ожидание,
обработка UNSEEN-писем (дедупликация через notmuch, канонизация,
доставка через ``fdm``). См. docs/ARCHITECTURE.md §2.3.

Запуск: инстанс ``threlium-bridge@email.service`` →
``python -m threlium.runners.bridge email`` (раннер передаёт ``deliver``).

Любая необработанная ошибка конвейера (IMAP, канонизация, ``fdm``) пробрасывается
в раннер → падение процесса, traceback в journald → ``systemd`` перезапускает
``threlium-bridge@email`` (``Restart=on-failure``).

Обязательные переменные окружения: ``THRELIUM_HOME``,
``THRELIUM_IMAP_HOST``, ``THRELIUM_IMAP_USER``, ``THRELIUM_IMAP_PASS``.
Опциональные: ``THRELIUM_IMAP_PORT`` (default 993/143 в зависимости от SSL),
``THRELIUM_IMAP_USE_SSL`` (default ``true``; ``false`` → plain IMAP),
``THRELIUM_IMAP_SSL_VERIFY`` (default ``1``; ``0`` для self-signed в e2e),
``THRELIUM_IMAP_IDLE_MAX_SEC`` (default 1740 ≈ 29 мин, RFC 2177).
"""
from __future__ import annotations

import imaplib
import ssl
import sys
from collections.abc import Callable, Iterable
from typing import Optional, Union, cast
from email import policy as email_policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, getaddresses

from imap_tools import A, MailBox, MailBoxUnencrypted
from imap_tools.errors import MailboxUidsError
from imap_tools.mailbox import BaseMailBox, Criteria
from imap_tools.message import MailMessage as ImapMailMessage
from imap_tools.utils import check_command_status

import threlium.nm as nm
from threlium.fsm_emit import HDR_HOP_BUDGET, HDR_ROUTE
from threlium.litellm_route_context import e2e_route_wire_tail
from threlium.delivery import fdm_bytes_from_message, run_fdm
from threlium.logutil import logger
from threlium.mime_reform import canonicalize_mime, ingress_raw_email_capture
from threlium.bridges import attach_raw_ingress_capture
from threlium.systemd_notify import notify_status
from threlium.types.systemd_status import SystemdStatusBody
from threlium.settings import ThreliumSettings
from threlium.types import (
    BridgeEmailSubjectLine,
    EmailIngressRoute,
    EmailNativeId,
    ExternalRfcMidWire,
    IngressRouteB62Wire,
    IrtHashWire,
    NotmuchMessageIdInner,
    RfcInReplyToWire,
    RfcMessageIdWire,
    RfcReferencesWire,
    RfcSenderWire,
    MailHeaderName,
)

_HDR = MailHeaderName

# Не копировать In-Reply-To / References из входящего до их явной перезаписи ниже.
# Иначе при ``out[hdr] = …`` политика EmailMessage добавляет второй одноимённый заголовок
# вместо замены первого → два In-Reply-To; ``msg.get()`` даёт внешний (первый), ломая notmuch.
_BRIDGE_CANONICAL_SKIP_LOWER = frozenset(
    h.value.lower()
    for h in (
        _HDR.FROM,
        _HDR.TO,
        _HDR.CC,
        _HDR.BCC,
        _HDR.REPLY_TO,
        _HDR.SENDER,
        _HDR.RETURN_PATH,
        _HDR.DELIVERED_TO,
        _HDR.DATE,
        _HDR.MESSAGE_ID,
        _HDR.SUBJECT,
        _HDR.IN_REPLY_TO,
        _HDR.REFERENCES,
    )
)

log = logger.bind(stage="bridge_email")


def _e2e_litellm_route_correlation(settings: ThreliumSettings) -> bool:
    return settings.e2e.litellm_route_correlation


class _UidSearchCharsetOptionalMixin:
    """Поддержка ``charset=None`` для поиска UID (как у ``fetch(..., charset=None)``).

    Выпущенный imap_tools (1.12.x) всегда шлёт ``UID SEARCH CHARSET …`` и ломается на
    ``.encode(None)``, если передать ``None``. Здесь: при явном ``charset is None`` —
    ``UID SEARCH`` без ``CHARSET`` (GreenMail и др.), иначе делегирование в базовый ``uids``.
    """

    _CHARSET_DEFAULT = object()

    def uids(
        self,
        criteria: Criteria = "ALL",
        charset: object = _CHARSET_DEFAULT,
        sort: Optional[Union[str, Iterable[str]]] = None,
    ) -> list[str]:
        if charset is self._CHARSET_DEFAULT:
            return cast(BaseMailBox, super()).uids(criteria, "US-ASCII", sort)
        if charset is not None:
            assert isinstance(charset, str)
            return cast(BaseMailBox, super()).uids(criteria, charset, sort)
        if sort:
            return cast(BaseMailBox, super()).uids(criteria, "US-ASCII", sort)
        encoded_criteria = criteria if type(criteria) is bytes else str(criteria).encode("ascii")
        uid_result = self.client.uid("SEARCH", encoded_criteria)  # type: ignore[arg-type]
        check_command_status(uid_result, MailboxUidsError)
        return uid_result[1][0].decode().split() if uid_result[1][0] else []


class _GracefulImapLogoutMixin:
    """Сервер может уже разорвать сокет до ``LOGOUT`` → ``imaplib`` бросает ``abort`` / ``OSError``.

    ``imap_tools.BaseMailBox.__exit__`` вызывает :meth:`logout` без перехвата; без этого
    миксина ``systemd`` видит traceback при штатном завершении ``threlium-bridge@email``.
    """

    def logout(self) -> tuple:  # type: ignore[override]
        try:
            return super().logout()  # type: ignore[misc]
        except (imaplib.IMAP4.abort, OSError) as exc:
            log.warning("imap_logout_ignored", error=repr(exc))
            return ("OK", [b""])


class BridgeMailBox(
    _GracefulImapLogoutMixin, _UidSearchCharsetOptionalMixin, MailBox
):
    """MailBox с учётом ``charset=None`` при поиске UID и терпимым ``logout``."""


class BridgeMailBoxUnencrypted(
    _GracefulImapLogoutMixin, _UidSearchCharsetOptionalMixin, MailBoxUnencrypted
):
    """MailBoxUnencrypted с учётом ``charset=None`` при поиске UID и терпимым ``logout``."""


def _origin_address(msg: EmailMessage) -> str:
    """Extract the raw sender address from the incoming message."""
    froms = msg.get_all(_HDR.FROM, [])
    reply_to = msg.get_all(_HDR.REPLY_TO, [])
    addrs = [a for _, a in getaddresses(froms + reply_to) if a]
    if not addrs:
        sender_w = RfcSenderWire.parse_present_from_email(msg, _HDR.SENDER)
        if sender_w is not None:
            sender = sender_w.value
            addrs = [a for _, a in getaddresses([sender]) if a]
    if not addrs:
        raise RuntimeError(
            "email bridge: incoming message has no From/Reply-To/Sender address"
        )
    return addrs[0]


def _first_angle_inner(s: str) -> str:
    s = str(s).strip()
    if s.startswith("<") and ">" in s:
        return s[1 : s.index(">")].strip()
    return s.strip("<>")


def _bridge_email_native_from_angle_inner(inner: str) -> EmailNativeId:
    """Email bridge→FSM: ``EmailNativeId(v=1)`` из inner угловых скобок (MESSAGES §2, email)."""
    s = str(inner).strip()
    if not s:
        raise ValueError("email bridge: empty Message-ID / In-Reply-To inner")
    return EmailNativeId(v=1, message_id=s)


def _bridge_wire_from_angle_inner(inner: str) -> RfcMessageIdWire:
    """Email bridge→FSM: wire MID/IRT через ``from_native(EmailNativeId)``."""
    return RfcMessageIdWire.from_native(_bridge_email_native_from_angle_inner(inner))


def _build_canonical(msg: EmailMessage, *, settings: ThreliumSettings) -> EmailMessage:
    """FSM-каноничное письмо: wire ``Message-ID`` / ``In-Reply-To`` / ``References`` через ``EmailNativeId(v=1)`` + ``from_native``."""
    origin = _origin_address(msg)
    mid_w = RfcMessageIdWire.parse_present_from_email(msg, _HDR.MESSAGE_ID)
    if mid_w is None:
        raise ValueError("incoming email has no Message-ID")
    prev_inner = mid_w.value.strip("<>")

    wire_mid = _bridge_wire_from_angle_inner(prev_inner).value

    refs_w = RfcReferencesWire.parse_present_from_email(msg, _HDR.REFERENCES)
    refs_src = refs_w.value if refs_w is not None else None
    irt_w = RfcInReplyToWire.parse_present_from_email(msg, _HDR.IN_REPLY_TO)
    subj_w = BridgeEmailSubjectLine.parse_present_from_email(msg, _HDR.SUBJECT)
    subject = subj_w.value if subj_w is not None else ""

    out = EmailMessage()
    skip = _BRIDGE_CANONICAL_SKIP_LOWER
    for k, v in msg.items():
        if k.lower() in skip:
            continue
        if k in out:
            out.add_header(k, v)
        else:
            out[k] = v

    payload = msg.get_payload(decode=False)
    if msg.is_multipart() and isinstance(payload, list):
        out.set_payload(payload)
        if msg.get(_HDR.CONTENT_TYPE) and _HDR.CONTENT_TYPE not in out:
            out[_HDR.CONTENT_TYPE] = msg.get(_HDR.CONTENT_TYPE)
        if msg.get(_HDR.MIME_VERSION) and _HDR.MIME_VERSION not in out:
            out[_HDR.MIME_VERSION] = msg.get(_HDR.MIME_VERSION)
    else:
        raw_body = msg.get_payload(decode=True)
        if isinstance(raw_body, bytes):
            charset = msg.get_content_charset() or "utf-8"
            subtype = (msg.get_content_subtype() or "plain").lower()
            out.set_content(raw_body.decode(charset, errors="replace"), subtype=subtype, charset=charset)
        else:
            out.set_content("" if payload is None else str(payload), subtype="plain", charset="utf-8")

    reply_tgt = ExternalRfcMidWire.parse_optional(mid_w.value)
    route_struct = EmailIngressRoute(
        channel="email",
        origin=origin,
        reply_target_rfc_message_id=reply_tgt,
    )
    route_wire = IngressRouteB62Wire.from_ingress_route(route_struct).value
    out[_HDR.FROM] = "email@localhost"
    out[_HDR.TO] = "ingress@localhost"
    out[_HDR.SUBJECT] = subject.replace("\n", " ").replace("\r", "")[:900]
    out[_HDR.DATE] = formatdate(localtime=True)
    out[_HDR.MESSAGE_ID] = wire_mid

    refs_canon = RfcReferencesWire.threlium_canonicalize_refs(
        refs_src, _bridge_email_native_from_angle_inner
    )
    if refs_canon.value.strip():
        out[_HDR.REFERENCES] = refs_canon.value
    if irt_w is not None:
        irt_val = _bridge_wire_from_angle_inner(
            _first_angle_inner(irt_w.value)
        ).value
        out[_HDR.IN_REPLY_TO] = irt_val
        out[_HDR.IRT_HASH] = IrtHashWire.from_irt_header_value(irt_val).value

    out[HDR_ROUTE] = route_wire
    if _e2e_litellm_route_correlation(settings):
        log.debug("e2e_bridge_canonical", origin=origin, inner_incoming_mid=prev_inner, route_tail=e2e_route_wire_tail(route_wire))
    out[HDR_HOP_BUDGET] = str(settings.hop.budget_root)
    raw_cap = ingress_raw_email_capture(msg)
    attach_raw_ingress_capture(out, raw_cap)
    return out


def rfc822_bytes_to_fsm_message(raw: bytes, *, settings: ThreliumSettings) -> EmailMessage:
    """Raw RFC822 → MIME canonicalize → FSM ``EmailMessage`` (wire mid/irt)."""
    incoming: EmailMessage = BytesParser(
        policy=email_policy.default
    ).parsebytes(raw)  # type: ignore[assignment]
    canonical = canonicalize_mime(incoming)
    return _build_canonical(canonical, settings=settings)


def rfc822_bytes_to_fsm_bytes(raw: bytes, *, settings: ThreliumSettings) -> bytes:
    """Pure pipeline: raw RFC822 → MIME canonicalize → FSM canonical → SMTP bytes."""
    return fdm_bytes_from_message(rfc822_bytes_to_fsm_message(raw, settings=settings))


def _is_duplicate(incoming_inner: str) -> bool:
    """Письмо с тем же входящим Message-ID уже есть в notmuch (канонический wire inner)."""
    mid_wire = _bridge_wire_from_angle_inner(incoming_inner)
    mid = NotmuchMessageIdInner.from_present_wire(mid_wire)
    return nm.notmuch_index_has_message_id(mid)


def _imap_fetch_full_by_uid(
    mailbox: BaseMailBox,
    uid: str,
    *,
    mark_seen: bool,
) -> ImapMailMessage:
    """Полное тело по UID через ``UID FETCH`` (без ``fetch(A(uid=…))`` → ``UID SEARCH UID …``).

    GreenMail отвечает ``BAD Search command not supported`` на критерий ``UID <n>`` внутри
    ``UID SEARCH`` (см. journal ``threlium-bridge-email``).
    """
    headers_only = False
    message_parts = (
        f"(BODY{'' if mark_seen else '.PEEK'}[{'HEADER' if headers_only else ''}] "
        "UID FLAGS RFC822.SIZE)"
    )
    raw = next(mailbox._fetch_by_one([uid], message_parts), None)
    if raw is None:
        raise RuntimeError(f"IMAP: UID FETCH для UID {uid} вернул пусто")
    return ImapMailMessage(raw)


def process_unseen_emails(
    mailbox: BaseMailBox,
    *,
    deliver: Callable[[EmailMessage], None] | None = None,
    settings: ThreliumSettings,
) -> None:
    """Fetch UNSEEN → dedup via notmuch → canonicalize → deliver → flag \\\\Seen.

    Ошибка ``deliver`` или инварианта — исключение наружу (раннер моста не ловит).
    """
    _deliver = deliver if deliver is not None else (
        lambda m: run_fdm(fdm_bytes_from_message(m))
    )
    for msg in mailbox.fetch(
        A(seen=False), charset=None, headers_only=True, mark_seen=False  # type: ignore[arg-type]
    ):
        uid_raw = msg.uid
        if uid_raw is None or not str(uid_raw).strip():
            raise RuntimeError("FSM-инвариант: UNSEEN без UID от IMAP")
        uid: str = str(uid_raw).strip()

        mid_values = msg.headers.get(_HDR.MESSAGE_ID.lower())
        if not mid_values or not mid_values[0]:
            raise RuntimeError(f"FSM-инвариант: UNSEEN UID {uid} без Message-ID")

        raw_mid = mid_values[0].strip()
        prev_inner = raw_mid.strip("<>")
        if not prev_inner:
            raise RuntimeError(f"FSM-инвариант: UNSEEN UID {uid} с пустым Message-ID")

        if _is_duplicate(prev_inner):
            log.info("duplicate_skip", message_id=prev_inner, uid=uid)
            if _e2e_litellm_route_correlation(settings):
                log.debug("e2e_bridge_duplicate_skip", inner_incoming_mid=prev_inner, uid=uid)
            mailbox.flag(uid, "\\Seen", True)
            continue

        full_msg = _imap_fetch_full_by_uid(mailbox, uid, mark_seen=False)

        notify_status(SystemdStatusBody.bridge_email_delivering_uid(uid=uid))

        data = rfc822_bytes_to_fsm_message(full_msg.obj.as_bytes(), settings=settings)
        route_w = IngressRouteB62Wire.parse_present_optional(data.get(HDR_ROUTE))
        dec = IngressRouteB62Wire.parse_route_from_optional_header(route_w)
        if dec is None:
            raise RuntimeError("FSM-инвариант: каноническое письмо без X-Threlium-Route")
        if not isinstance(dec, EmailIngressRoute):
            raise RuntimeError(
                "FSM-инвариант: ожидался EmailIngressRoute в X-Threlium-Route, "
                f"получен {type(dec).__name__} (channel={dec.channel!r})"
            )
        _deliver(data)

        mailbox.flag(uid, "\\Seen", True)
        if _e2e_litellm_route_correlation(settings):
            rw = data.get(HDR_ROUTE)
            log.debug("e2e_bridge_delivered", inner_incoming_mid=prev_inner, uid=uid, route_tail=e2e_route_wire_tail(rw if isinstance(rw, str) else None))
        log.info("delivered", message_id=prev_inner)
        notify_status(SystemdStatusBody.bridge_email_connected_idle_simple())


def run_bridge(deliver: Callable[[EmailMessage], None], *, settings: ThreliumSettings) -> None:
    """Точка входа моста: вызывается из ``python -m threlium.runners.bridge email``."""
    if not str(settings.home):
        log.error("threlium_home_required")
        sys.exit(1)

    email_cfg = settings.bridges.email
    host = email_cfg.imap_host
    user = email_cfg.imap_user
    password = email_cfg.imap_pass

    missing = [
        k
        for k, v in [
            ("imap_host", host),
            ("imap_user", user),
            ("imap_pass", password),
        ]
        if not v
    ]
    if missing:
        log.error("required_settings_missing", keys=missing)
        sys.exit(1)

    assert host and user and password
    use_ssl = email_cfg.imap_use_ssl
    verify = email_cfg.imap_ssl_verify
    port = email_cfg.imap_port or (993 if use_ssl else 143)
    idle_timeout = email_cfg.imap_idle_max_sec

    if use_ssl:
        ctx: ssl.SSLContext | None = None
        if not verify:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        box: BridgeMailBox | BridgeMailBoxUnencrypted = BridgeMailBox(
            host, port=port, ssl_context=ctx
        )
    else:
        box = BridgeMailBoxUnencrypted(host, port=port)
    with box.login(
        user, password, initial_folder="INBOX"  # type: ignore[arg-type]
    ) as mailbox:
        log.info("connected", host=host, port=port)
        notify_status(
            SystemdStatusBody.bridge_email_connected_idle(host=host, port=port)
        )
        process_unseen_emails(mailbox, deliver=deliver, settings=settings)

        while True:
            responses = mailbox.idle.wait(timeout=idle_timeout)
            if responses:
                log.info("idle_events", count=len(responses))
            process_unseen_emails(mailbox, deliver=deliver, settings=settings)
