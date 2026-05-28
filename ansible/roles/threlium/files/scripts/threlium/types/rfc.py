"""RFC822 wire VO (strip на границе) и Threlium-кодек Message-ID (MESSAGES §2)."""
from __future__ import annotations

import re
from email.utils import make_msgid
from typing import Callable, Self

import base62  # pybase62
import msgspec

from .identity import (
    EmailNativeId,
    ExternalRfcMidWire,
    NativeId,
    TNative,
)
from ._core import _OptionalStripEmpty

_MSGID_RE = re.compile(r"<\s*([^<>]+)\s*>")
_CANONICAL_MSGID_RE = re.compile(
    r"\s*<([0-9A-Za-z]+)@localhost>\s*",
)
_INTERNAL = "localhost"
_MARKERS = (_INTERNAL,)

_ENCODER = msgspec.json.Encoder()


class RfcInReplyToWire(_OptionalStripEmpty):
    """Значение заголовка ``In-Reply-To`` (wire) после strip."""


class RfcFromWire(_OptionalStripEmpty):
    """Значение заголовка ``From`` (wire) после strip."""


class RfcToWire(_OptionalStripEmpty):
    """Значение заголовка ``To`` (wire) после strip."""


class RfcDateWire(_OptionalStripEmpty):
    """Значение заголовка ``Date`` (wire) после strip."""


class RfcSenderWire(_OptionalStripEmpty):
    """Значение заголовка ``Sender`` (wire) после strip."""


class RfcSubjectWire(_OptionalStripEmpty):
    """Значение заголовка ``Subject`` (wire) после strip."""


class RfcMessageIdWire(_OptionalStripEmpty):
    """Заголовок ``Message-ID`` (wire) после strip; кодек MESSAGES §2."""

    @classmethod
    def parse_threlium_canonical_optional(cls, raw: str | None) -> Self | None:
        """Сырое ``Message-ID`` / ``In-Reply-To`` → wire ``<b62@localhost>`` или ``None``."""
        if raw is None:
            return None
        s = str(raw).strip()
        if not s:
            return None
        m = _CANONICAL_MSGID_RE.search(s)
        if not m:
            return None
        return cls.parse_present_optional(f"<{m.group(1)}@{_INTERNAL}>")

    @classmethod
    def from_native(cls, native: NativeId, *, internal: bool = False) -> Self:
        _ = internal  # §9: эмиссия только ``@localhost``; флаг оставлен для совместимости вызовов.
        marker = _INTERNAL
        payload = _ENCODER.encode(native)
        assert isinstance(payload, bytes)
        left = base62.encodebytes(payload)
        s = f"<{left}@{marker}>"
        w = cls.parse_present_optional(s)
        if w is None:
            raise RuntimeError("from_native: produced empty Message-ID wire")
        return w

    @classmethod
    def native_from_canonical_str(
        cls,
        canonical_msgid: str,
        native_type: type[TNative],
    ) -> TNative:
        m = _MSGID_RE.fullmatch(canonical_msgid.strip())
        if not m:
            raise ValueError(f"malformed msgid: {canonical_msgid!r}")
        inner = m.group(1).strip()
        left, _, right = inner.rpartition("@")
        if right not in _MARKERS:
            raise ValueError(f"non-canonical msgid: {canonical_msgid!r}")
        payload = base62.decodebytes(left)
        return msgspec.json.decode(payload, type=native_type)


    @classmethod
    def internal_for_fsm(cls) -> Self:
        """Новый ``Message-ID`` для FSM: ``<b62(EmailNativeId)@localhost>``."""
        native = EmailNativeId(
            v=1,
            message_id=make_msgid(domain=_INTERNAL).strip().strip("<>"),
        )
        return cls.from_native(native, internal=True)

    @classmethod
    def threlium_fs_id_left(cls, canonical_msgid: str) -> str:
        """Локальная часть (b62) каноничного ``<…@localhost>`` (без ``@`` и домена)."""
        m = _MSGID_RE.fullmatch(canonical_msgid.strip())
        if not m:
            raise ValueError(f"malformed msgid: {canonical_msgid!r}")
        inner = m.group(1).strip()
        left, _, right = inner.rpartition("@")
        if right not in _MARKERS:
            raise ValueError(f"non-canonical msgid: {canonical_msgid!r}")
        return left


class RfcReferencesWire(_OptionalStripEmpty):
    """Значение заголовка ``References`` после границы strip."""

    @classmethod
    def threlium_canonicalize_refs(
        cls,
        hdr: str | Self | None,
        make: Callable[[str], NativeId],
    ) -> Self:
        """Каждое ``<inner>`` в ``References`` → Threlium ``Message-ID`` (native через ``make``)."""
        raw = _references_hdr_raw(hdr).strip()
        if not raw:
            return cls.parse(None)
        parts: list[str] = []
        for m in re.finditer(r"<([^>]+)>", raw):
            inner = m.group(1).strip()
            if not inner:
                continue
            w = RfcMessageIdWire.from_native(make(inner))
            parts.append(w.value)
        return cls.parse(" ".join(parts) if parts else None)

    @classmethod
    def threlium_decanonicalize_refs(
        cls,
        hdr: str | Self | None,
        typ: type[NativeId],
    ) -> Self:
        """Каноничные Threlium id в ``References`` → внешние ``<message_id>`` (пока только email)."""
        raw = _references_hdr_raw(hdr).strip()
        if not raw:
            return cls.parse(None)
        out: list[str] = []
        for m in re.finditer(r"<[^>]+>", raw):
            native = RfcMessageIdWire.native_from_canonical_str(m.group(0), native_type=typ)
            if isinstance(native, EmailNativeId):
                out.append(f"<{native.message_id}>")
            else:
                raise TypeError(
                    f"threlium_decanonicalize_refs: unsupported type {typ!r}"
                )
        return cls.parse(" ".join(out) if out else None)


def _references_hdr_raw(hdr: object) -> str:
    if hdr is None:
        return ""
    if isinstance(hdr, RfcReferencesWire):
        return hdr.value
    return str(hdr)


def references_angle_bracket_tokens(hdr: str) -> list[str]:
    """Список токенов ``<…>`` в заголовке ``References`` (порядок сохраняется)."""
    return re.findall(r"<[^>]+>", str(hdr))


def truncate_rfc_references_wire(refs: RfcReferencesWire, max_len: int = 8000) -> RfcReferencesWire:
    """Усечь ``References`` по длине, не разрывая токены ``<…>`` (слева отбрасываются старые id)."""
    s = refs.value.strip()
    if len(s) <= max_len:
        return refs
    tokens = references_angle_bracket_tokens(s)
    if not tokens:
        return RfcReferencesWire.parse(s[:max_len])
    while len(tokens) > 1 and len(" ".join(tokens)) > max_len:
        tokens.pop(0)
    joined = " ".join(tokens).strip()
    if len(joined) <= max_len:
        return RfcReferencesWire.parse(joined if joined else None)
    one = tokens[-1]
    if len(one) <= max_len:
        return RfcReferencesWire.parse(one)
    return RfcReferencesWire.parse(one[:max_len])


class CanonicalMidWire(msgspec.Struct, frozen=True):
    """Каноничный MID/IRT внутри FSM (wire ``<b62@localhost>``)."""

    value: str

    @classmethod
    def assert_from_wire(cls, w: RfcMessageIdWire) -> Self:
        if RfcMessageIdWire.parse_threlium_canonical_optional(w.value) is None:
            raise ValueError(f"expected canonical Message-ID wire, got {w.value!r}")
        return cls(value=w.value.strip())
