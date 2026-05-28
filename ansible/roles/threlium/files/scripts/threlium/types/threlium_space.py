"""Пространство диалога канала: VO + b62-wire + SHA256-hash для ``X-Threlium-Space-Hash``.

Отдельный доменный смысл: «где живёт диалог» (чат, комната) — не маршрут
(:class:`~threlium.types.identity.TelegramIngressRoute`) и не идентичность
одного сообщения (:class:`~threlium.types.identity.TelegramNativeId`).

b62-кодек инкапсулирован внутри :class:`ThreliumSpaceB62Wire` (как
:class:`~threlium.types.ingress.IngressRouteB62Wire` для маршрута).
SHA256-хеш для индекса Xapian — :class:`ThreliumSpaceHashWire`.
"""
from __future__ import annotations

import hashlib
from typing import Self, cast

import base62
import msgspec

from ._core import NonEmptyStr, _OptionalStripEmpty
from .identity import (
    MatrixIngressRoute,
    MatrixRoomId,
    TelegramIngressRoute,
)
from .notmuch_query import NotmuchIndexedHeader


class TelegramSpaceV1(msgspec.Struct, frozen=True):
    channel: NonEmptyStr
    v: int
    chat_id: int
    message_thread_id: int | None


class MatrixSpaceV1(msgspec.Struct, frozen=True):
    channel: NonEmptyStr
    v: int
    room_id: MatrixRoomId


ThreliumSpace = TelegramSpaceV1 | MatrixSpaceV1

_OPTIONAL_STR_KEYS_EMPTY_TO_NONE = frozenset({"message_thread_id"})


def normalize_threlium_space_dict(d: dict[str, object]) -> dict[str, object]:
    """Единственная фабрика нормализации dict → dict перед ``msgspec.json.decode``."""
    out: dict[str, object] = {}
    for k, v in d.items():
        if isinstance(v, str):
            t = v.strip()
            if k in _OPTIONAL_STR_KEYS_EMPTY_TO_NONE and not t:
                out[k] = None
            elif not t:
                raise msgspec.ValidationError(
                    f"threlium space field {k!r} is empty or whitespace-only"
                )
            else:
                out[k] = t
        else:
            out[k] = v
    return out


def _threlium_space_from_json_str(raw: str) -> ThreliumSpace:
    blob = raw.encode("utf-8")
    probe = msgspec.json.decode(blob)
    if not isinstance(probe, dict):
        raise msgspec.ValidationError("threlium space JSON must be an object")
    norm = normalize_threlium_space_dict(cast(dict[str, object], probe))
    blob2 = msgspec.json.encode(norm)
    ch = norm.get("channel")
    if ch == "telegram":
        return msgspec.json.decode(blob2, type=TelegramSpaceV1)
    if ch == "matrix":
        return msgspec.json.decode(blob2, type=MatrixSpaceV1)
    raise ValueError(f"unknown threlium space channel: {ch!r}")


class ThreliumSpaceB62Wire(_OptionalStripEmpty):
    """Wire b62(JSON) пространства диалога (кодек: encode/decode :class:`ThreliumSpace`).

    Заголовок ``X-Threlium-Space-Id`` больше не индексируется (>64 символов);
    для поиска используется :class:`ThreliumSpaceHashWire` (``X-Threlium-Space-Hash``).
    """

    @classmethod
    def from_threlium_space(cls, space: ThreliumSpace) -> Self:
        """Типизированное пространство → wire b62(JSON)."""
        raw = msgspec.json.encode(space)
        assert isinstance(raw, bytes)
        return cls(value=base62.encodebytes(raw))

    def to_threlium_space(self) -> ThreliumSpace:
        """Wire b62 → :class:`ThreliumSpace`."""
        json_str = base62.decodebytes(self.value).decode("utf-8")
        return _threlium_space_from_json_str(json_str)

    @classmethod
    def decode_b62_wire(cls, b62_wire: str) -> ThreliumSpace:
        """Сырая b62-строка (содержимое заголовка) → :class:`ThreliumSpace`."""
        s = str(b62_wire).strip()
        return cls(value=s).to_threlium_space()

    def space_hash_wire(self) -> ThreliumSpaceHashWire:
        """SHA256-хеш wire для записи в ``X-Threlium-Space-Hash``."""
        return ThreliumSpaceHashWire.from_space_b62_wire(self)

    def as_notmuch_index_term(self) -> str:
        """Терм notmuch-запроса ``Threliumspacehash:"<hex>"`` (делегирует в hash-VO)."""
        return self.space_hash_wire().as_notmuch_index_term()


class ThreliumSpaceHashWire(_OptionalStripEmpty):
    """Wire ``X-Threlium-Space-Hash`` (sha256 hex) после strip.

    Xapian ``MAX_PROB_TERM_LENGTH = 64``: base62 wire (96+ символов) отбрасывается;
    SHA256 hex (ровно 64 символа) проходит лимит.
    Фабрика ``from_space_b62_wire`` — единственная точка вычисления хеша.
    """

    @classmethod
    def from_space_b62_wire(cls, wire: ThreliumSpaceB62Wire) -> Self:
        """b62 wire пространства → SHA256 hex wire."""
        return cls(value=hashlib.sha256(wire.value.encode()).hexdigest())

    def as_notmuch_index_term(self) -> str:
        """``Threliumspacehash:"<hex>"`` — notmuch search term."""
        return NotmuchIndexedHeader.SPACE_HASH.term(self.value)


def telegram_space_from_ingress_route(r: TelegramIngressRoute) -> TelegramSpaceV1:
    """Space из маршрута Telegram (только поля пространства, без checkpoint)."""
    return TelegramSpaceV1(
        channel="telegram", v=1,
        chat_id=r.chat_id, message_thread_id=r.message_thread_id,
    )


def matrix_space_from_room_id(room_id: MatrixRoomId) -> MatrixSpaceV1:
    """Space из ``room_id`` Matrix."""
    return MatrixSpaceV1(channel="matrix", v=1, room_id=room_id)


def matrix_space_from_ingress_route(r: MatrixIngressRoute) -> MatrixSpaceV1:
    """Space из маршрута Matrix."""
    return matrix_space_from_room_id(r.room_id)
