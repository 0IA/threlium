"""Per-route OpenAI-compatible tool-specs для стадии reasoning.

Контракт (см. план «Шаблоны LLM и LightRAG», блок D):

* Каждый возможный маршрут FSM (:data:`~threlium.types.reasoning.REASONING_TARGET_STAGES`)
  — отдельный tool со своим JSON-Schema. Шаблоны в ``prompts/reasoning/<stage>/``.
* Имя tool'а ОБЯЗАНО совпадать с ``FsmStage.value`` целевой стадии.
* Python: рендер шаблонов, jsonschema, :class:`~threlium.types.reasoning.ReasoningRouteDecision`.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import cast

import jsonschema
import msgspec

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
from threlium.types.reasoning import (
    ReasoningToolCallArgumentsWire,
    ReasoningToolFunctionName,
)


def load_tools_for_routes(
    routes: Iterable[FsmStage],
) -> tuple[list[dict[str, object]], dict[FsmStage, dict[str, object]]]:
    """Собрать tool-specs и schemas для перечисленных целевых стадий."""
    tools: list[dict[str, object]] = []
    schemas: dict[FsmStage, dict[str, object]] = {}
    for route in routes:
        spec_path = REASONING_TOOL_SPEC_BY_STAGE[route]
        rendered = render_prompt(spec_path)
        raw = json.loads(rendered)
        if not isinstance(raw, dict):
            raise RuntimeError(f"{spec_path}: tool spec JSON must be an object")
        spec = cast(dict[str, object], raw)
        func = spec["function"]
        if not isinstance(func, dict):
            raise RuntimeError(f"{spec_path}: function must be an object")
        fn = cast(dict[str, object], func)
        name_o = fn.get("name")
        params_o = fn.get("parameters")
        if not isinstance(name_o, str):
            raise RuntimeError(f"{spec_path}: function.name must be a string")
        if not isinstance(params_o, dict):
            raise RuntimeError(f"{spec_path}: function.parameters must be an object")
        params = cast(dict[str, object], params_o)
        if name_o != route.value:
            raise RuntimeError(
                f"{spec_path}: function.name={name_o!r}; "
                "имя инструмента должно совпадать с FsmStage.value целевой стадии"
            )
        tools.append(spec)
        schemas[route] = params
    return tools, schemas


def validate_tool_args(
    route: FsmStage,
    schema: dict[str, object],
    wire: ReasoningToolCallArgumentsWire,
) -> ReasoningToolRouteArgs:
    """Распарсить wire JSON, провалидировать schema, привести к Struct маршрута."""
    raw_args = wire.value
    args = json.loads(raw_args)
    jsonschema.validate(instance=args, schema=schema)
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
    wire: ReasoningToolCallArgumentsWire,
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
