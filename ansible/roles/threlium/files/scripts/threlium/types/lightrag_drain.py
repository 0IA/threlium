"""Причины пропуска письма drain'ом LightRAG (тег ``lightrag_skipped``).

Замкнутый набор для структурированных логов: почему письмо помечено
:attr:`~threlium.types.notmuch_tag.NotmuchTag.LIGHTRAG_SKIPPED` вместо
``lightrag_indexed`` (нет ``<history>``-части по :func:`~threlium.mime_reform.message_has_history`
или упал рендер) — терминальное решение drain.
"""
from __future__ import annotations

from enum import StrEnum


class LightragDrainSkipReason(StrEnum):
    """Причина, по которой drain пометил письмо ``lightrag_skipped`` (для логов)."""

    RENDER_FAILED = "render_failed"
    # Письмо без <history>-части (только <system>/control): индексировать как контекст нечем.
    # Ожидаемо, не дрейф селектора — селектор намеренно даёт лишь tag-негативы.
    NO_HISTORY = "no_history"
