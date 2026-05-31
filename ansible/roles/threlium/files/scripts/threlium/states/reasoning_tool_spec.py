"""Per-route OpenAI-compatible tool-specs для стадии reasoning.

Контракт (см. план «Шаблоны LLM и LightRAG», блок D):

* Каждый возможный маршрут FSM (:data:`~threlium.types.reasoning.REASONING_TARGET_STAGES`)
  — отдельный tool со своим JSON-Schema. Шаблоны в ``prompts/reasoning/<stage>/``.
* Имя tool'а ОБЯЗАНО совпадать с ``FsmStage.value`` целевой стадии.
* Python: рендер шаблонов, jsonschema, :class:`~threlium.types.reasoning.ReasoningRouteDecision`.
"""
from __future__ import annotations

from collections.abc import Iterable

import msgspec

from threlium.litellm_tool_spec import (
    load_tool_spec,
    tool_spec_parameters,
    validate_tool_args_json,
)
from threlium.prompts import render_prompt
from threlium.types import (
    FsmStage,
    REASONING_EMAIL_BODY_BY_STAGE,
    REASONING_EMAIL_SUBJECT_BY_STAGE,
    REASONING_TOOL_SPEC_BY_STAGE,
    ReasoningRouteDecision,
    ReasoningToolRouteArgs,
    ReasoningToolRouteEmailBody,
    ReasoningToolRouteEmailSubject,
    reasoning_tool_struct_for_route,
)
from threlium.types import LiteLlmToolCallArgumentsWire
from threlium.types.reasoning import ReasoningToolFunctionName


def load_tools_for_routes(
    routes: Iterable[FsmStage],
) -> tuple[list[dict[str, object]], dict[FsmStage, dict[str, object]]]:
    """Собрать tool-specs и schemas для перечисленных целевых стадий."""
    tools: list[dict[str, object]] = []
    schemas: dict[FsmStage, dict[str, object]] = {}
    for route in routes:
        spec_path = REASONING_TOOL_SPEC_BY_STAGE[route]
        spec = load_tool_spec(spec_path)
        func = spec["function"]
        if not isinstance(func, dict):
            raise RuntimeError(f"{spec_path}: function must be an object")
        name_o = func.get("name")
        if not isinstance(name_o, str):
            raise RuntimeError(f"{spec_path}: function.name must be a string")
        if name_o != route.value:
            raise RuntimeError(
                f"{spec_path}: function.name={name_o!r}; "
                "имя инструмента должно совпадать с FsmStage.value целевой стадии"
            )
        tools.append(spec)
        schemas[route] = tool_spec_parameters(spec)
    return tools, schemas


def validate_tool_args(
    route: FsmStage,
    schema: dict[str, object],
    wire: LiteLlmToolCallArgumentsWire,
) -> ReasoningToolRouteArgs:
    """Распарсить wire JSON, провалидировать schema, привести к Struct маршрута."""
    args = validate_tool_args_json(schema, wire)
    struct_t = reasoning_tool_struct_for_route(route)
    return msgspec.convert(args, type=struct_t)


def render_route_decision(
    route: FsmStage, args: ReasoningToolRouteArgs
) -> ReasoningRouteDecision:
    """Отрендерить subject/body для следующей стадии из аргументов tool'а."""
    kw = msgspec.to_builtins(args)
    subject = render_prompt(
        REASONING_EMAIL_SUBJECT_BY_STAGE[route], **kw
    ).strip()
    body = render_prompt(
        REASONING_EMAIL_BODY_BY_STAGE[route], **kw
    ).rstrip("\n")
    return ReasoningRouteDecision.from_rendered(route, subject=subject, body=body)


def route_decision_from_tool_call(
    tool_name: ReasoningToolFunctionName,
    wire: LiteLlmToolCallArgumentsWire,
    schemas: dict[FsmStage, dict[str, object]],
) -> ReasoningRouteDecision:
    """Полный путь tool_call → :class:`ReasoningRouteDecision`."""
    route = tool_name.target_stage()
    args = validate_tool_args(route, schemas[route], wire)
    return render_route_decision(route, args)


__all__ = [
    "load_tools_for_routes",
    "render_route_decision",
    "route_decision_from_tool_call",
    "validate_tool_args",
]
