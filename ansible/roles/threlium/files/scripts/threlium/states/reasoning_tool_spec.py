"""Per-route OpenAI-compatible tool-specs для стадии reasoning.

Контракт (см. план «Шаблоны LLM и LightRAG», блок D):

* Каждый возможный маршрут FSM (`ROUTE_TO_ADDRESS` в
  :mod:`threlium.states.reasoning`) — это **отдельный** tool со своим
  JSON-Schema. Шаблоны живут в ``$THRELIUM_HOME/prompts/reasoning/<route>/``
  тройкой ``tool_spec.j2`` / ``email_body.j2`` / ``email_subject.j2``.
* Имя tool'а ОБЯЗАНО совпадать с ключом маршрута
  (``function.name == route_key``); :func:`load_tools_for_routes` проверяет
  это инвариантом и падает RuntimeError'ом, если оператор сломал
  именование при правке шаблона.
* Python только: рендерит шаблоны, валидирует ответ модели через
  ``jsonschema``, рендерит ``subject`` / ``body`` для исходящего письма
  следующей стадии. Сам JSON-Schema полностью контролируется
  пользователем через шаблон ``tool_spec.j2``.
"""
from __future__ import annotations

import json

from typing import cast

import jsonschema
import msgspec

from threlium.prompts import render_prompt
from threlium.types import (
    FsmStage,
    REASONING_EMAIL_BODY_BY_STAGE,
    REASONING_EMAIL_SUBJECT_BY_STAGE,
    REASONING_TOOL_SPEC_BY_STAGE,
)
from threlium.types.reasoning_tool_args import (
    ReasoningToolRouteArgs,
    reasoning_tool_struct_for_route,
)


def load_tools_for_routes(
    route_keys: list[str],
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    """Собрать tool-specs и schemas для перечисленных маршрутов.

    Возвращает кортеж ``(tools, schemas_by_name)``:

    * ``tools`` — список tool-spec'ов в формате OpenAI; передаётся в
      ``litellm.completion(tools=...)``.
    * ``schemas_by_name[function.name]`` — объект ``parameters`` каждого
      tool'а, готовый к ``jsonschema.validate(args, schema)``.

    Имя tool'а ОБЯЗАНО совпадать с ``route_key``: если шаблон
    ``reasoning/<route>/tool_spec.j2`` задаёт ``function.name``,
    отличное от ``route``, поднимается ``RuntimeError`` (защита от
    неосторожной правки оператором).
    """
    tools: list[dict[str, object]] = []
    schemas: dict[str, dict[str, object]] = {}
    for route in route_keys:
        target = FsmStage(route)
        spec_path = REASONING_TOOL_SPEC_BY_STAGE[target]
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
        name = name_o
        if not isinstance(params_o, dict):
            raise RuntimeError(f"{spec_path}: function.parameters must be an object")
        params = cast(dict[str, object], params_o)
        if name != route:
            raise RuntimeError(
                f"{spec_path}: function.name={name!r}; "
                "имя инструмента должно совпадать с ключом маршрута"
            )
        tools.append(spec)
        schemas[name] = params
    return tools, schemas


def validate_tool_args(
    route: str, schema: dict[str, object], raw_args: str | bytes
) -> ReasoningToolRouteArgs:
    """Распарсить ``raw_args`` (JSON от LLM), провалидировать schema и привести к Struct.

    Бросает ``json.JSONDecodeError`` или ``jsonschema.ValidationError`` при
    невалидном JSON или несоответствии схеме (проброс наружу без обёртки).
    ``msgspec.ValidationError`` — если dict после jsonschema не совпадает с доменным Struct.
    """
    if isinstance(raw_args, bytes):
        raw_args = raw_args.decode("utf-8", errors="replace")
    args = json.loads(raw_args)
    jsonschema.validate(instance=args, schema=schema)
    struct_t = reasoning_tool_struct_for_route(route)
    return msgspec.convert(args, type=struct_t)


def render_route_email(route: str, args: ReasoningToolRouteArgs) -> tuple[str, str]:
    """Отрендерить ``(subject, body)`` для следующей стадии из аргументов tool'а.

    Шаблоны ``reasoning/<route>/email_subject.j2`` и
    ``email_body.j2`` получают поля Struct как Jinja2-переменные;
    ``StrictUndefined`` ловит несоответствие ``required``-полей
    шаблону на этапе рендера.
    """
    target = FsmStage(route)
    kw = msgspec.to_builtins(args)
    subject = render_prompt(
        REASONING_EMAIL_SUBJECT_BY_STAGE[target], **kw
    ).strip()
    body = render_prompt(
        REASONING_EMAIL_BODY_BY_STAGE[target], **kw
    ).rstrip("\n")
    return subject, body


__all__ = [
    "load_tools_for_routes",
    "validate_tool_args",
    "render_route_email",
]
