"""Hop-budget (wire-строки)."""
from __future__ import annotations

from ._core import _OptionalStripEmpty, _OptionalStripNone


class HopBudgetLine(_OptionalStripEmpty):
    """Одна строка ``X-Threlium-Hop-Budget`` после ``strip``."""


class HopTailToken(_OptionalStripEmpty):
    """Один токен хвоста hop-budget после ``strip``."""


class XThreliumHopBudgetHeaderWireOptional(_OptionalStripNone):
    """Необязательное значение ``X-Threlium-Hop-Budget`` (reasoning / fatigue)."""
