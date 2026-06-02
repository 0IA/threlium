"""Аргументы tool-вызовов стадии ``enrich`` (task plan / query plan).

После :class:`~threlium.types.litellm_tool_call.LiteLlmToolCallArgumentsWire`
и ``validate_tool_args_json`` — см. ``enrich_tool_bridge``. Отдельные VO на каждый
tool (DDD, ``docs/TYPES.md`` § VO), даже при схожей форме.
"""
from __future__ import annotations

import msgspec


class EnrichTaskPlanToolArgs(msgspec.Struct, frozen=True):
    """Seed-подзадачи (``<task-init>``): список коротких формулировок."""

    subtasks: list[str]


class EnrichQueryPlanToolArgs(msgspec.Struct, frozen=True):
    """Сформулированный запрос к графу LightRAG для ``aquery``."""

    formulated_query: str


__all__ = ["EnrichQueryPlanToolArgs", "EnrichTaskPlanToolArgs"]
