"""Notmuch2 и union-notmuch wire VO."""
from __future__ import annotations

from typing import Self

import msgspec
import notmuch2  # pyright: ignore[reportMissingImports]

from ._core import _OptionalStripEmpty, _OptionalStripLowerEmpty, _OptionalStripNone
from .notmuch_query import NotmuchQueryField
from .rfc import RfcMessageIdWire


class NotmuchMessageIds(msgspec.Struct, frozen=True, kw_only=True):
    """Поля ``messageid`` / ``threadid`` объекта notmuch2."""

    messageid: RfcMessageIdWire | None = None
    threadid: NotmuchThreadScopeId | None = None

    @classmethod
    def from_notmuch(cls, msg: notmuch2.Message) -> Self:
        mid_raw = msg.messageid
        tid_raw = msg.threadid
        return cls(
            messageid=RfcMessageIdWire.parse_present_optional(
                None if mid_raw is None else str(mid_raw)
            ),
            threadid=NotmuchThreadScopeId.parse_present_optional(
                None if tid_raw is None else str(tid_raw)
            ),
        )


class NotmuchQuerySortFlag(_OptionalStripLowerEmpty):
    """Параметр ``sort`` для notmuch-запросов (wire после strip+lower в ``.value``)."""

    @property
    def is_newest_first(self) -> bool:
        return self.value in ("newest-first", "newest_first")


class NotmuchThreadScopeId(_OptionalStripEmpty):
    """Идентификатор треда notmuch для barrier / scope.

    ``.value`` — **суффикс** после ``thread:`` в notmuch-запросах (как в ``%i`` воркера
    и в ``thread:<id>``); префикс ``thread:`` в wire не хранится.
    """

    @classmethod
    def from_notmuch_thread_attr(cls, tid: object) -> Self | None:
        """Нормализация ``threadid`` libnotmuch → present-or-None."""
        if tid is None:
            return None
        s = str(tid).strip()
        if not s:
            return None
        full = s if s.startswith("thread:") else f"thread:{s}"
        bare = full[len("thread:") :] if full.startswith("thread:") else full
        return cls.parse_present_optional(bare)

    @classmethod
    def require_from_notmuch_thread_attr(cls, tid: object) -> Self:
        """``threadid`` как ИНВАРИАНТ notmuch: у проиндексированного письма thread-id всегда есть.

        None недостижим штатно: cffi-NULL под устаревшей ревизией бросает ``RuntimeError`` на самом
        ``msg.threadid`` (property) ДО сюда (discard → ловит ``read_retry``/resume); пустой/None tid для
        индексированного письма невозможен. None здесь = нарушение инварианта/повреждение индекса →
        ``RuntimeError`` (fail-closed, НЕ fallback на «unknown»-тред)."""
        out = cls.from_notmuch_thread_attr(tid)
        if out is None:
            raise RuntimeError("notmuch invariant violated: indexed message has no thread id")
        return out

    def as_notmuch_thread_term(self) -> str:
        """Термин запроса ``thread:<id>`` (notmuch search)."""
        return NotmuchQueryField.THREAD.term(self.value)


class UnionNotmuchRouteHeaderWire(_OptionalStripNone):
    """``X-Threlium-Route`` из заголовков файла при индексации union-notmuch."""


class UnionNotmuchFromHeaderWire(_OptionalStripNone):
    """``From`` из заголовков файла при индексации union-notmuch."""
