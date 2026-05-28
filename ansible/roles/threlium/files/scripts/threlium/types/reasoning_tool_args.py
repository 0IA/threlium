"""msgspec-модели аргументов OpenAI tool_calls для маршрутов стадии reasoning.

После jsonschema.validate входной dict приводится к Struct тем же контрактом, что и ``docs/TYPES.md``
уровень 1 (без повторной «очистки» в роутере).
"""
from __future__ import annotations

from typing import Union

import msgspec


class EgressRouterToolArgs(msgspec.Struct, frozen=True):
    subject: str
    body: str


class CliIntentToolArgs(msgspec.Struct, frozen=True):
    argv: list[str]
    reasoning: str
    cwd: str | None = None


class ThreadMemoryToolArgs(msgspec.Struct, frozen=True):
    reasoning: str
    note: str


class GlobalMemoryToolArgs(msgspec.Struct, frozen=True):
    reasoning: str
    note: str


class SubagentIntentToolArgs(msgspec.Struct, frozen=True):
    reasoning: str
    task: str


class ReflectToolArgs(msgspec.Struct, frozen=True):
    reasoning: str
    summary: str
    clarification_request: str


class ResponseAppendToolArgs(msgspec.Struct, frozen=True):
    reasoning: str
    content: str


class ResponseEditToolArgs(msgspec.Struct, frozen=True):
    reasoning: str
    position: int
    new_content: str | None = None


class ResponseObserveToolArgs(msgspec.Struct, frozen=True):
    reasoning: str


class ResponseFinalizeToolArgs(msgspec.Struct, frozen=True):
    reasoning: str
    subject: str
    verification_summary: str
    content: str | None = None


ReasoningToolRouteArgs = Union[
    CliIntentToolArgs,
    ThreadMemoryToolArgs,
    GlobalMemoryToolArgs,
    SubagentIntentToolArgs,
    ReflectToolArgs,
    ResponseAppendToolArgs,
    ResponseEditToolArgs,
    ResponseObserveToolArgs,
    ResponseFinalizeToolArgs,
]


_REASONING_ROUTE_STRUCTS: dict[str, type[msgspec.Struct]] = {
    "cli_intent": CliIntentToolArgs,
    "thread_memory": ThreadMemoryToolArgs,
    "global_memory": GlobalMemoryToolArgs,
    "subagent_intent": SubagentIntentToolArgs,
    "reflect": ReflectToolArgs,
    "response_append": ResponseAppendToolArgs,
    "response_edit": ResponseEditToolArgs,
    "response_observe": ResponseObserveToolArgs,
    "response_finalize": ResponseFinalizeToolArgs,
}


def reasoning_tool_struct_for_route(route: str) -> type[msgspec.Struct]:
    """Тип Struct для маршрута ``route`` (ключ ``ROUTE_TO_ADDRESS``)."""
    t = _REASONING_ROUTE_STRUCTS.get(route)
    if t is None:
        raise ValueError(f"unknown reasoning route: {route!r}")
    return t


__all__ = [
    "CliIntentToolArgs",
    "EgressRouterToolArgs",
    "GlobalMemoryToolArgs",
    "ReasoningToolRouteArgs",
    "ReflectToolArgs",
    "ResponseAppendToolArgs",
    "ResponseEditToolArgs",
    "ResponseFinalizeToolArgs",
    "ResponseObserveToolArgs",
    "SubagentIntentToolArgs",
    "ThreadMemoryToolArgs",
    "reasoning_tool_struct_for_route",
]
