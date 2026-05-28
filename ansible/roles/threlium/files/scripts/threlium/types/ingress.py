"""Ingress wire и ``EmailMessage`` → проекции (фабрика A, только VO)."""
from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from typing import Self, cast

import base62
import msgspec

from threlium.mime_reform import parse_rfc822

from ._core import _OptionalStripEmpty
from threlium.mail_header_names import MailHeaderName
from .identity import (
    EmailIngressRoute,
    IngressRoute,
    MatrixIngressRoute,
    TelegramIngressRoute,
    normalize_ingress_route_dict,
)
from .rfc import RfcInReplyToWire, RfcReferencesWire

_HDR = MailHeaderName


def ingress_route_from_json_str(raw: str) -> IngressRoute:
    """UTF-8 JSON (после b62 decode) → типизированный ingress-маршрут."""
    blob = raw.encode("utf-8")
    # Декод без ``type=dict``: объект → ``normalize_ingress_route_dict`` — единственная
    # фабрика нормализации перед ``msgspec.json.decode`` в union-маршрут (``docs/TYPES.md``).
    probe = msgspec.json.decode(blob)
    if not isinstance(probe, dict):
        raise msgspec.ValidationError("ingress route JSON must be an object")
    norm = normalize_ingress_route_dict(cast(dict[str, object], probe))
    blob2 = msgspec.json.encode(norm)
    ch = norm.get("channel")
    if ch == "email":
        return msgspec.json.decode(blob2, type=EmailIngressRoute)
    if ch == "telegram":
        return msgspec.json.decode(blob2, type=TelegramIngressRoute)
    if ch == "matrix":
        return msgspec.json.decode(blob2, type=MatrixIngressRoute)
    raise ValueError(f"unknown ingress route channel: {ch!r}")


class IngressRouteB62Wire(_OptionalStripEmpty):
    """Wire ``X-Threlium-Route`` (b62 JSON) после strip."""

    @classmethod
    def from_ingress_route(cls, route: IngressRoute) -> Self:
        """Типизированный маршрут → wire b62(JSON) для ``X-Threlium-Route``."""
        raw = msgspec.json.encode(route)
        assert isinstance(raw, bytes)
        return cls(value=base62.encodebytes(raw))

    def to_ingress_route(self) -> IngressRoute:
        """Wire-значение b62 → :class:`IngressRoute`."""
        json_str = base62.decodebytes(self.value).decode("utf-8")
        return ingress_route_from_json_str(json_str)

    @classmethod
    def decode_b62_wire(cls, b62_wire: str) -> IngressRoute:
        """Сырая b62-строка (содержимое заголовка) → :class:`IngressRoute`."""
        s = str(b62_wire).strip()
        return cls(value=s).to_ingress_route()

    @classmethod
    def parse_route_from_optional_header(cls, wire: Self | None) -> IngressRoute | None:
        """``X-Threlium-Route`` (VO) → маршрут; пусто / ``None`` → ``None``."""
        if wire is None:
            return None
        s = str(wire.value).strip()
        if not s:
            return None
        return cls(value=s).to_ingress_route()


class EmailStruct:
    """Миксин: ``from_message`` → ``msgspec.convert`` по полям ``Struct``."""

    @classmethod
    def from_message(cls, msg: EmailMessage) -> Self:
        raw: dict[str, str] = {}
        for field in msgspec.structs.fields(cls):
            val = msg.get(field.encode_name)
            if val is not None:
                cleaned = str(val).strip()
                if cleaned:
                    raw[field.encode_name] = cleaned
        return msgspec.convert(raw, type=cls)

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        """``parse_rfc822`` → ``from_message``."""
        return cls.from_message(parse_rfc822(data))

    @classmethod
    def from_file(cls, path: Path | str) -> Self:
        """Чтение файла Maildir и разбор как у :meth:`from_bytes`."""
        return cls.from_bytes(Path(path).read_bytes())


class IngressRouterChildMsg(msgspec.Struct, frozen=True, kw_only=True):
    """Вход ``ingress_router``: дочернее письмо (ветвление по ``In-Reply-To``)."""

    in_reply_to: RfcInReplyToWire | None

    @classmethod
    def from_email(cls, msg: EmailMessage) -> Self:
        return cls(
            in_reply_to=RfcInReplyToWire.parse_present_from_email(msg, _HDR.IN_REPLY_TO),
        )

    @classmethod
    def from_message(cls, msg: EmailMessage) -> Self:
        return cls.from_email(msg)

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        return cls.from_email(parse_rfc822(data))

    @classmethod
    def from_file(cls, path: Path | str) -> Self:
        return cls.from_bytes(Path(path).read_bytes())


class ReferencesInReplyHeaders(msgspec.Struct, frozen=True, kw_only=True):
    """Заголовки ``References`` / ``In-Reply-To`` для обхода предков по ``In-Reply-To``.

    ``references`` на промежуточном MIME FSM обычно отсутствует (заголовок не переносится между стадиями);
    резолв маршрута опирается только на каноничный ``in_reply_to``.
    """

    references: RfcReferencesWire | None
    in_reply_to: RfcInReplyToWire | None

    @classmethod
    def from_email(cls, msg: EmailMessage) -> Self:
        return cls(
            references=RfcReferencesWire.parse_present_from_email(msg, _HDR.REFERENCES),
            in_reply_to=RfcInReplyToWire.parse_present_from_email(msg, _HDR.IN_REPLY_TO),
        )

    @classmethod
    def from_message(cls, msg: EmailMessage) -> Self:
        return cls.from_email(msg)
