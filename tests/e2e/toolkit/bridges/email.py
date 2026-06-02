"""Email bridge wire helpers (Message-ID, notmuch, route b62)."""
from __future__ import annotations

import re
import uuid
from email.header import decode_header
from typing import Any

from threlium.types import (
    EmailIngressRoute,
    EmailNativeId,
    ExternalRfcMidWire,
    IngressRouteB62Wire,
    NotmuchMessageIdInner,
    RfcMessageIdWire,
)

def rfc_first_message_id_in_in_reply_to_header(value: str | None) -> str | None:
    """Первый токен ``<…>`` из заголовка ``In-Reply-To`` (RFC 5322). Пусто → ``None``."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    m = re.search(r"<([^>]+)>", s)
    return m.group(1).strip() if m else None


def canonical_external_msgid(raw_message_id: str) -> str:
    """Возвращает каноничный inner-id для внешнего email Message-ID (без ``<…>``).

    `docs/MESSAGES.md` §2: на границе ingress для email входящий
    ``Message-ID:`` приводится к ``<base62(EmailNativeId{v:1, message_id:<raw>})@localhost>`` через
    :meth:`RfcMessageIdWire.from_native`;
    для **email-моста** в индекс попадает :func:`email_ingress_notmuch_id_inner`, не эта форма.
    Для проверок notmuch после моста используйте :func:`email_ingress_notmuch_id_inner`.
    """
    canonical_full = RfcMessageIdWire.from_native(
        EmailNativeId(v=1, message_id=raw_message_id)
    ).value
    return f"{RfcMessageIdWire.threlium_fs_id_left(canonical_full)}@localhost"


def email_ingress_notmuch_id_inner(raw_message_id: str) -> str:
    """Inner ``Message-ID`` в union-notmuch после email-моста и индексации продуктом.

    Мост заменяет входящий идентификатор на :meth:`RfcMessageIdWire.from_native`
    с :class:`EmailNativeId` (``v=1``, строка ``inner`` из угловых скобок), как в
    :func:`threlium.bridges.email._bridge_wire_from_angle_inner`.
    """
    inner = raw_message_id.strip().strip("<>")
    return (
        RfcMessageIdWire.from_native(EmailNativeId(v=1, message_id=inner))
        .value.strip("<>")
        .strip()
    )


def notmuch_id_search_term(inner_or_bracketed: str) -> str:
    """Предикат ``id:"…"`` для ``notmuch count/search`` (экранирование inner ``Message-ID``)."""
    mid = NotmuchMessageIdInner.from_optional_raw(inner_or_bracketed)
    if mid is None:
        raise ValueError(f"notmuch_id_search_term: invalid message-id: {inner_or_bracketed!r}")
    return mid.as_notmuch_term()


def e2e_smtp_inject_ingress_route_wire_for_message_id(
    *,
    raw_message_id: str,
    origin: str = "pytest@localhost",
) -> str:
    """B62-wire ``X-Threlium-Route`` как после IMAP-моста (см. ``bridges.email._build_canonical``).

    ``raw_message_id`` — inner ``Message-ID`` инъекции (как в ``smtp_inject`` / ``--message-id``), со или без
    угловых скобок. ``origin`` — адрес отправителя (по умолчанию как в ``smtp_inject.py``).
    Для ``wiremock_state_seed_context`` и проверок LiteLLM используйте эту строку: она совпадает с
    ``reply_target_rfc_message_id`` + ``origin`` в JSON маршрута на письме в notmuch.
    """
    rt = ExternalRfcMidWire.parse_optional(str(raw_message_id).strip())
    route = EmailIngressRoute(
        channel="email",
        origin=str(origin).strip(),
        reply_target_rfc_message_id=rt,
    )
    return IngressRouteB62Wire.from_ingress_route(route).value.strip()


def e2e_thread_root_mid_for_message_id(raw_message_id: str) -> str:
    """Уголковый ``Message-ID`` старейшего в notmuch-треде письма с ``tag:route`` (как ``X-Threlium-Thread-Root``).

    Продукт берёт то же значение, что ``resolved.message_id_inner`` у
    :func:`~threlium.ingress_route_resolve.resolve_route_from_thread_oldest_route_tag_under_db`
    (один коррелятор на весь тред, все каналы). Тест подбирает ``raw_message_id`` / вход стаба
    так, что после ингресса этим ``Message-ID`` оказывается именно то письмо; для SMTP-инъекции
    это каноника ``email_ingress_notmuch_id_inner`` (короче route wire — лимит WireMock State).
    """
    inner = email_ingress_notmuch_id_inner(raw_message_id)
    return f"<{inner}>"


def e2e_smtp_inject_ingress_route_wire() -> str:
    """Устаревший b62 **без** ``reply_target_rfc_message_id`` (в JSON будет ``null``).

    **Не совпадает** с реальным ``X-Threlium-Route`` после моста для SMTP-инъекции: мост всегда кладёт
    ``reply_target_rfc_message_id`` из входящего ``Message-ID``. Для WireMock State и корреляции
    LiteLLM вызывайте :func:`e2e_smtp_inject_ingress_route_wire_for_message_id`.

    Оставлен для редких проверок «только origin» / обратной совместимости импортов.
    """
    return IngressRouteB62Wire.from_ingress_route(
        EmailIngressRoute(channel="email", origin="pytest@localhost")
    ).value.strip()
