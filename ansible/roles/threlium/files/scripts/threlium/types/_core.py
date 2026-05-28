"""Приватное ядро VO: ``NonEmptyStr``, ``_kv_*``, базы ``_*`` (не в ``threlium.types.__all__``)."""
from __future__ import annotations

from email.message import EmailMessage
from typing import Annotated, Self, TypeAlias

import msgspec
import notmuch2  # pyright: ignore[reportMissingImports]

NonEmptyStr: TypeAlias = Annotated[str, msgspec.Meta(min_length=1)]


def _kv_dict(value: str | None, key: str) -> dict[str, str]:
    d: dict[str, str] = {}
    if value is not None:
        t = str(value).strip()
        if t:
            d[key] = t
    return d


def _kv_dict_lower(value: str | None, key: str) -> dict[str, str]:
    """Как :func:`_kv_dict`, но после ``strip`` применяется ``lower`` (параметры запросов)."""
    d: dict[str, str] = {}
    if value is not None:
        t = str(value).strip().lower()
        if t:
            d[key] = t
    return d


class _OptionalStripEmpty(msgspec.Struct, frozen=True, kw_only=True):
    """Strip → непустое в dict; иначе ключ отсутствует → ``value`` по умолчанию ``\"\"``."""

    value: str = ""

    @classmethod
    def parse(cls, raw: str | None) -> Self:
        return msgspec.convert(_kv_dict(raw, "value"), type=cls)

    @classmethod
    def parse_present_optional(cls, raw: str | None) -> Self | None:
        """Отсутствие / strip-пусто → ``None``; иначе wire с **непустым** ``value`` (present-or-None)."""
        if raw is None:
            return None
        t = str(raw).strip()
        if not t:
            return None
        out = msgspec.convert(_kv_dict(t, "value"), type=cls)
        if not out.value:
            return None
        return out

    @classmethod
    def parse_present_from_email(cls, msg: EmailMessage, header_name: str) -> Self | None:
        """``EmailMessage.get`` + present-or-None (strip); нет заголовка / пусто → ``None``."""
        val = msg.get(header_name)
        if val is None:
            return None
        return cls.parse_present_optional(str(val))

    @classmethod
    def parse_present_from_nm_message(cls, msg: notmuch2.Message, header_name: str) -> Self | None:
        """``notmuch2.Message.header`` + present-or-None (``LookupError`` как отсутствие заголовка)."""
        try:
            raw = msg.header(header_name)
        except LookupError:
            return None
        return cls.parse_present_optional(str(raw))


class _OptionalStripLowerEmpty(msgspec.Struct, frozen=True, kw_only=True):
    """Strip + ``lower`` → непустое в dict; иначе ключ отсутствует → ``value`` по умолчанию ``\"\"``."""

    value: str = ""

    @classmethod
    def parse(cls, raw: str | None) -> Self:
        return msgspec.convert(_kv_dict_lower(raw, "value"), type=cls)

    @classmethod
    def parse_present_optional(cls, raw: str | None) -> Self | None:
        if raw is None:
            return None
        t = str(raw).strip().lower()
        if not t:
            return None
        out = msgspec.convert(_kv_dict_lower(t, "value"), type=cls)
        if not out.value:
            return None
        return out

    @classmethod
    def parse_present_from_email(cls, msg: EmailMessage, header_name: str) -> Self | None:
        """Как у :meth:`_OptionalStripEmpty.parse_present_from_email`, с ``strip`` + ``lower``."""

        val = msg.get(header_name)
        if val is None:
            return None
        return cls.parse_present_optional(str(val))

    @classmethod
    def parse_present_from_nm_message(cls, msg: notmuch2.Message, header_name: str) -> Self | None:
        """Как у :meth:`_OptionalStripEmpty.parse_present_from_nm_message`, с ``strip`` + ``lower``."""

        try:
            raw = msg.header(header_name)
        except LookupError:
            return None
        return cls.parse_present_optional(str(raw))


class _OptionalStripNone(msgspec.Struct, frozen=True, kw_only=True):
    """Strip → непустое в dict; иначе ``value is None``."""

    value: str | None = None

    @classmethod
    def parse(cls, raw: str | None) -> Self:
        return msgspec.convert(_kv_dict(raw, "value"), type=cls)


class _RequiredNonEmpty(msgspec.Struct, frozen=True, kw_only=True):
    """Обязательная непустая строка после strip → ``ValueError``."""

    value: NonEmptyStr

    @classmethod
    def require(cls, *, name: str, raw: str | None) -> Self:
        try:
            return msgspec.convert(_kv_dict(raw, "value"), type=cls)
        except msgspec.ValidationError as e:
            raise ValueError(f"{name}: missing or empty after strip") from e


