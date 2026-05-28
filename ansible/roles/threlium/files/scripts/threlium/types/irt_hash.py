"""SHA256-хеш ``In-Reply-To`` для индексируемого ``X-Threlium-Irt-Hash``.

Xapian ``MAX_PROB_TERM_LENGTH = 64``: base62-encoded MID (96+ символов)
молча отбрасывается; SHA256 hex (ровно 64 символа) проходит лимит.
b62-кодек инкапсулирован: фабрика ``from_irt_header_value`` — единственная точка
вычисления хеша; обратное декодирование невозможно.
"""
from __future__ import annotations

import hashlib
from typing import Self

from ._core import _OptionalStripEmpty
from .notmuch_query import NotmuchIndexedHeader


class IrtHashWire(_OptionalStripEmpty):
    """Wire ``X-Threlium-Irt-Hash`` (sha256 hex) после strip.

    Фабрика ``from_irt_header_value`` — хеш от полного значения ``In-Reply-To``
    (``<mid@domain>``).  Обратное декодирование невозможно (односторонний хеш).
    """

    @classmethod
    def from_irt_header_value(cls, irt: str) -> Self:
        """``In-Reply-To`` значение (``<mid@domain>``) → SHA256 hex wire."""
        return cls(value=hashlib.sha256(irt.strip().encode()).hexdigest())

    def as_notmuch_index_term(self) -> str:
        """``Threliumirthash:"<hex>"`` — notmuch search term."""
        return NotmuchIndexedHeader.IRT_HASH.term(self.value)
