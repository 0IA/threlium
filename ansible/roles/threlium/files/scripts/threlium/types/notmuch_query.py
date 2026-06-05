"""Литералы и сборка notmuch search: ``AND``/``OR``/``NOT``, префиксы ``thread:``/``to:``/…."""
from __future__ import annotations

from enum import StrEnum
from typing import Sequence


class NotmuchQueryConnective(StrEnum):
    """Булевы операторы notmuch search (wire — ключевое слово без пробелов)."""

    AND = "AND"
    OR = "OR"
    NOT = "NOT"

    def spaced(self) -> str:
        """Оператор с пробелами слева и справа (`` AND ``, `` OR ``)."""
        return f" {self.value} "

    @classmethod
    def join_and(cls, *parts: str) -> str:
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return cls.AND.spaced().join(parts)

    @classmethod
    def join_or(cls, *parts: str) -> str:
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return cls.OR.spaced().join(parts)

    @classmethod
    def negate(cls, term: str) -> str:
        """``NOT <term>`` (без окружающих ``AND``)."""
        return f"{cls.NOT.value} {term}"


class NotmuchQueryField(StrEnum):
    """Префиксы не-tag предикатов notmuch (часть до ``:``)."""

    THREAD = "thread"
    TO = "to"
    FROM = "from"
    FOLDER = "folder"

    def term(self, value: str, *, quoted: bool = False) -> str:
        """``field:value``; для ``folder:`` обычно ``quoted=True`` (путь с ``/``)."""
        if quoted:
            return f'{self.value}:"{value}"'
        return f"{self.value}:{value}"


class NotmuchQuery:
    """Составные шаблоны (скобки + ``OR`` + ``AND``)."""

    @staticmethod
    def group(term: str) -> str:
        return f"({term})"

    @staticmethod
    def or_terms_and_rhs(or_terms: Sequence[str], rhs: str) -> str:
        """``(t1 OR t2 OR …) AND rhs``."""
        inner = NotmuchQueryConnective.join_or(*or_terms)
        return NotmuchQueryConnective.join_and(NotmuchQuery.group(inner), rhs)


class NotmuchBridgeFromLocalhost(StrEnum):
    """Локальные ``From:`` мостов в union-notmuch (см. fdm.conf / ``docs/INDEX``)."""

    TELEGRAM = "telegram@localhost"
    MATRIX = "matrix@localhost"
    EMAIL = "email@localhost"
    ISOMORPH = "isomorph@localhost"

    def as_from_query_term(self) -> str:
        """Предикат ``from:<mailbox>``."""
        return NotmuchQueryField.FROM.term(self.value)


class NotmuchIndexedHeader(StrEnum):
    """Xapian-prefix custom indexed headers (notmuch-config ``[index] header.*``).

    Значения -- имена prefix'ов, синхронизированные с ``notmuch-config.j2``.
    """

    SPACE_HASH = "Threliumspacehash"
    IRT_HASH = "Threliumirthash"

    def term(self, value: str) -> str:
        """``<prefix>:"<escaped>"`` -- запрос по indexed header."""
        escaped = value.replace('"', '""')
        return f'{self.value}:"{escaped}"'
