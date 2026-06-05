"""Контент-адресуемый хеш isomorph-сообщения (git-style ``content_hash`` в Message-ID).

Канонический ``<b62@localhost>`` строится из :class:`~threlium.types.identity.IsomorphContentId`,
поле ``content_hash`` которого — sha256-hex от **нормализованного** контента ассистент-сообщения.

Один и тот же модуль нормализации применяется в ДВУХ местах (байтовый паритет MID обязателен):
  * ``states/egress_isomorph`` — от ответа, который произвёл Threlium (egress glue Message-ID);
  * ``bridges/isomorph/history`` — от last-assistant входящей истории (``In-Reply-To`` нового хода)
    и от хвоста (``Message-ID`` нового ingress).

Нормализация (см. docs/THREAD_MODEL §isomorph): только text-блоки + сигнатура tool_use
(``name`` + canonical-json ``arguments`` + опц. ``tool_id``); thinking/reasoning и ``cache_control``
исключены. ``tool_id`` включается только там, где он echo-стабилен (Anthropic ``tool_use.id``);
на OpenAI он усекается SDK — передавать пустую строку.
"""
from __future__ import annotations

import hashlib
import json
from typing import Self

import msgspec

from ._core import _OptionalStripEmpty


def canonical_json(obj: object) -> str:
    """Детерминированная каноническая форма JSON (сортировка ключей, без пробелов).

    Единственная точка канонизации ``arguments`` tool-вызова — чтобы egress и мост
    считали байт-идентичный хеш для одного и того же логического контента.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class IsomorphToolCallSig(msgspec.Struct, frozen=True):
    """Нормализованная сигнатура одного tool-вызова ассистента."""

    name: str
    #: Каноническая JSON-строка аргументов (через :func:`canonical_json`).
    arguments: str
    #: ``tool_use.id`` — только если echo-стабилен (Anthropic); иначе ``""``.
    tool_id: str = ""


class IsomorphAssistantContent(msgspec.Struct, frozen=True):
    """Нейтральный контент ассистент-сообщения для контент-адресации.

    Обе стороны (egress-completion и распарсенный wire last-assistant) сводятся к этому
    представлению; ``content_hash`` детерминирован от него.
    """

    text: str
    tool_calls: tuple[IsomorphToolCallSig, ...] = ()

    def content_hash(self) -> IsomorphContentHashWire:
        return IsomorphContentHashWire.from_content(self)


class IsomorphContentHashWire(_OptionalStripEmpty):
    """sha256-hex от нормализованного контента; значение → ``IsomorphContentId.content_hash``."""

    @classmethod
    def from_content(cls, content: IsomorphAssistantContent) -> Self:
        """Хеш ответа ассистента — egress glue Message-ID И ``In-Reply-To`` следующего хода (паритет)."""
        tool_calls = sorted(
            [tc.name, tc.arguments, tc.tool_id] for tc in content.tool_calls
        )
        payload = canonical_json({"kind": "assistant", "v": 1, "text": content.text, "tool_calls": tool_calls})
        return cls(value=hashlib.sha256(payload.encode("utf-8")).hexdigest())

    @classmethod
    def from_ingress_tail(cls, *, parent: str, tail: str) -> Self:
        """Хеш ingress-хвоста → ``Message-ID`` нового ingress.

        Включает ``parent`` (``In-Reply-To``), чтобы один и тот же хвост под разными родителями
        давал разные MID (позиционная уникальность), а ретрай того же запроса — тот же MID
        (идемпотентность). ``kind`` отделяет namespace от ассистент-хеша.
        """
        payload = canonical_json({"kind": "ingress", "v": 1, "parent": parent, "tail": tail})
        return cls(value=hashlib.sha256(payload.encode("utf-8")).hexdigest())
