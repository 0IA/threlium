"""Протокол JSON по одной строке: submit → ``threlium.runners.engine`` (UNIX stream).

Wire-типы вынесены в лёгкий :mod:`threlium.enginewire` (без зависимости от пакета
``threlium.types``), чтобы submitter (``threlium-work@`` на каждый FSM-hop) не платил
за импорт litellm. Здесь — реэкспорт для обратной совместимости (``threlium.types``-API
и ``docs/ORCHESTRATION.md``/``docs/TYPES.md`` уровень 1).
"""
from __future__ import annotations

from threlium.enginewire import EngineWireError, EngineWireOk, EngineWireRequest

__all__ = ["EngineWireError", "EngineWireOk", "EngineWireRequest"]
