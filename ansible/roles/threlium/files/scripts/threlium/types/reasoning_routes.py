"""Целевые стадии маршрутов ``reasoning`` (без тяжёлых зависимостей).

Отдельный модуль, чтобы :mod:`prompt_path` и :mod:`reasoning_tool_args` не
тянули ``enrich_context`` / ``nm`` через цикл импорта ``types``.
"""
from __future__ import annotations

from .fsm_stage import FsmStage

REASONING_TARGET_STAGES: frozenset[FsmStage] = frozenset(
    {
        FsmStage.CLI_INTENT,
        FsmStage.THREAD_MEMORY,
        FsmStage.GLOBAL_MEMORY,
        FsmStage.SUBAGENT_INTENT,
        FsmStage.REFLECT,
        FsmStage.RESPONSE_APPEND,
        FsmStage.RESPONSE_EDIT,
        FsmStage.RESPONSE_OBSERVE,
        FsmStage.RESPONSE_FINALIZE,
        FsmStage.FORMAL_REASON,
        FsmStage.MEMORY_QUERY,
        FsmStage.TASKS_UPSERT,
    }
)

__all__ = ["REASONING_TARGET_STAGES"]
