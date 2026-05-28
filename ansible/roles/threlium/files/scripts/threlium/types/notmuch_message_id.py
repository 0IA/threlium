"""Notmuch inner ``Message-ID``: нормализованный id без ``<>`` для ``db.find`` / ``as_notmuch_term()``."""
from __future__ import annotations

from typing import Self

import msgspec

from .fsm_strings import MessageIdHeaderNormalizationInput
from .rfc import RfcInReplyToWire, RfcMessageIdWire

_PresentMidHeaderWire = RfcMessageIdWire | RfcInReplyToWire


def _inner_str_from_headerish_raw(raw: str | None) -> str | None:
    t = MessageIdHeaderNormalizationInput.parse(raw).value
    if t is None:
        return None
    s = t
    if s.startswith("<") and s.endswith(">"):
        s = s[1:-1].strip()
    return s if s else None


class NotmuchMessageIdInner(msgspec.Struct, frozen=True):
    """Inner ``messageid`` notmuch2 (без угловых скобок), см. ``docs/INDEX.md`` §10."""

    value: str

    @classmethod
    def from_optional_raw(cls, raw: str | None) -> Self | None:
        """Сырой заголовок / произвольная строка до нормализации → inner или ``None``."""
        inner = _inner_str_from_headerish_raw(raw)
        if inner is None:
            return None
        return cls(inner)

    @classmethod
    def from_present_mid_header_wire(cls, wire: _PresentMidHeaderWire) -> Self:
        """Present ``RfcMessageIdWire`` / ``RfcInReplyToWire`` → inner (без распаковки у вызывающего)."""
        out = cls.from_optional_raw(wire.value)
        if out is None:
            raise ValueError("from_present_mid_header_wire: empty after normalize")
        return out

    @classmethod
    def from_present_wire(cls, wire: RfcMessageIdWire) -> Self:
        """Алиас для ``Message-ID``-wire."""
        return cls.from_present_mid_header_wire(wire)

    @classmethod
    def from_optional_wire(cls, wire: RfcMessageIdWire | None) -> Self | None:
        if wire is None:
            return None
        return cls.from_optional_raw(wire.value)

    def equals_case_insensitive(self, other: Self) -> bool:
        return self.value == other.value or self.value.lower() == other.value.lower()

    def as_angle_bracket_header(self) -> str:
        """Значение для RFC822 ``Message-ID`` / ``In-Reply-To`` с угловыми скобками."""
        inner = self.value.strip().strip("<>")
        return f"<{inner}>"

    def as_rfc_in_reply_to_wire(self) -> RfcInReplyToWire:
        """Inner id → VO заголовка ``In-Reply-To`` для слота ``reply_to_mid`` при FSM emit."""
        w = RfcInReplyToWire.parse_present_optional(self.as_angle_bracket_header())
        if w is None:
            raise ValueError("as_rfc_in_reply_to_wire: empty after normalize")
        return w

    def as_notmuch_term(self) -> str:
        """Предикат notmuch search для inner id: ``id:"…"`` с удвоением ``"`` внутри.

        См. грамматику notmuch/Xapian: внутри ``id:"…"`` каждый литеральный ``"`` в
        ``Message-ID`` записывается как ``""`` (не ``\\"``). Так корректно ищутся id
        с ``)`` / пробелами / кавычками; не используйте сырой ``f"id:{value}"`` —
        он ломает ``(id:…) OR …`` и парсер при спецсимволах.
        """
        escaped = self.value.replace('"', '""')
        return f'id:"{escaped}"'
