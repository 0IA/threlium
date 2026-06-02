"""Wire-значение ``X-Threlium-Call-Site`` для reasoning (umbrella).

Reasoning — единственный chat-вызов с переменным числом tools в ``tools`` (multi-tool
router; при budget-exhausted остаётся один ``response_finalize``). Все его HTTP-вызовы
помечаются единым call_site ``reasoning`` — отдельным от ingress / enrich / observe /
lightrag / cli. Инвариант «один tool = function.name» к ``reasoning`` не применяется
(см. :func:`~threlium.litellm_client._assert_single_tool_call_site`).
"""
from __future__ import annotations

from threlium.types import LitellmCallSite


def reasoning_call_site_wire() -> str:
    return LitellmCallSite.REASONING.value


__all__ = ["reasoning_call_site_wire"]
