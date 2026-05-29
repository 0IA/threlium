"""Причины пропуска письма drain'ом LightRAG (тег ``lightrag_skipped``).

Замкнутый набор для структурированных логов: почему письмо помечено
:attr:`~threlium.types.notmuch_tag.NotmuchTag.LIGHTRAG_SKIPPED` вместо
``lightrag_indexed``. Не дублирует :class:`~threlium.context_budget.ContextMessageType`
(тот про роль в enrich-контексте), здесь — про терминальное решение drain.
"""
from __future__ import annotations

from enum import StrEnum


class LightragDrainSkipReason(StrEnum):
    """Причина, по которой drain пометил письмо ``lightrag_skipped`` (для логов)."""

    RENDER_FAILED = "render_failed"
    SELECTOR_DRIFT = "selector_drift"
