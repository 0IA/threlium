"""Hop-budget и capabilities (wire-строки)."""
from __future__ import annotations

from ._core import _OptionalStripEmpty, _OptionalStripNone


class HopBudgetLine(_OptionalStripEmpty):
    """Одна строка ``X-Threlium-Hop-Budget`` после ``strip``."""


class HopTailToken(_OptionalStripEmpty):
    """Один токен хвоста hop-budget после ``strip``."""


class CapabilityTailToken(_OptionalStripEmpty):
    """Один токен хвоста ``X-Threlium-Capabilities`` (тот же wire-формат, что hop-tail)."""


class ThreliumCapabilitiesBudgetLine(_OptionalStripEmpty):
    """Одна строка ``X-Threlium-Capabilities`` после strip (стек токенов по пробелам, как hop-budget)."""


class XThreliumHopBudgetHeaderWireOptional(_OptionalStripNone):
    """Необязательное значение ``X-Threlium-Hop-Budget`` (reasoning / fatigue)."""
