"""Загрузка Jinja tool_spec и валидация аргументов tool_calls (общий для reasoning / LightRAG)."""
from __future__ import annotations

import json
from typing import cast

import jsonschema
import jsonschema.protocols
import jsonschema.validators
from threlium.llm_wire import LlmToolCall as ChatCompletionMessageToolCall, LlmAssistantMessage as Message

from threlium.logutil import logger
from threlium.prompts import render_prompt
from threlium.types import PromptPath
from threlium.types.litellm_tool_call import LiteLlmToolCallArgumentsWire

log = logger.bind(component="tool_spec")

# Кэш jsonschema-валидаторов по СОДЕРЖИМОМУ схемы. ``jsonschema.validate`` на КАЖДЫЙ вызов гонит
# ``validator_for`` + ``check_schema`` (мета-валидация схемы — ~1мс, 96% стоимости, замерено) + сборку
# валидатора. Схемы tool-spec статичны (из закоммиченного JSON), поэтому валидатор собирается ОДИН раз на
# уникальную схему; сам ``validator.validate(instance)`` ~0.01мс → 87x. Прогревается на старте engine
# (:func:`warm_tool_specs`), так что в steady-state — только cache-hit. Ключ — стабильная сериализация
# схемы (а не ``id``), чтобы не зависеть от того, переиспользуется ли dict-объект схемы между вызовами.
_VALIDATOR_CACHE: dict[str, jsonschema.protocols.Validator] = {}


def _cached_validator(schema: dict[str, object]) -> jsonschema.protocols.Validator:
    key = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    validator = _VALIDATOR_CACHE.get(key)
    if validator is None:
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)  # мета-валидация схемы — ОДИН раз на сборку, не на вызов
        validator = validator_cls(schema)
        _VALIDATOR_CACHE[key] = validator
    return validator


def load_tool_spec(prompt_path: PromptPath, /, **jinja_vars: object) -> dict[str, object]:
    """Собрать один OpenAI tool dict из ``tool_spec.j2``.

    ``jinja_vars`` — переменные шаблона (напр. ``distill_max_chars`` для ingress_distill);
    единая точка загрузки tool-spec с валидацией ``function.name`` / ``function.parameters``.
    """
    rendered = render_prompt(prompt_path, **jinja_vars)
    raw = json.loads(rendered)
    if not isinstance(raw, dict):
        raise RuntimeError(f"{prompt_path}: tool spec JSON must be an object")
    spec = cast(dict[str, object], raw)
    func = spec.get("function")
    if not isinstance(func, dict):
        raise RuntimeError(f"{prompt_path}: function must be an object")
    fn = cast(dict[str, object], func)
    name_o = fn.get("name")
    params_o = fn.get("parameters")
    if not isinstance(name_o, str) or not name_o.strip():
        raise RuntimeError(f"{prompt_path}: function.name must be a non-empty string")
    if not isinstance(params_o, dict):
        raise RuntimeError(f"{prompt_path}: function.parameters must be an object")
    return spec


def tool_spec_parameters(spec: dict[str, object]) -> dict[str, object]:
    """JSON Schema из загруженного tool spec."""
    func = spec["function"]
    if not isinstance(func, dict):
        raise RuntimeError("tool spec: function must be an object")
    params = func.get("parameters")
    if not isinstance(params, dict):
        raise RuntimeError("tool spec: function.parameters must be an object")
    return cast(dict[str, object], params)


def first_tool_call(msg: Message) -> ChatCompletionMessageToolCall | None:
    """Первый tool_call из assistant message (или None)."""
    tcs = msg.tool_calls
    if not tcs:
        return None
    return tcs[0]


def tool_call_arguments_wire_from_tool_call(
    tc: ChatCompletionMessageToolCall,
) -> LiteLlmToolCallArgumentsWire:
    """Сырой JSON args из tool_call (общий wire-класс)."""
    return LiteLlmToolCallArgumentsWire.from_tool_call(tc)


def validate_tool_args_json(
    schema: dict[str, object],
    wire: LiteLlmToolCallArgumentsWire,
) -> dict[str, object]:
    """Валидация wire-args против схемы КЭШИРОВАННЫМ валидатором (без per-call check_schema/build) → dict.

    ``ValidationError`` поднимается так же, как ``jsonschema.validate`` (доменные bridge ловят его)."""
    args = json.loads(wire.value)
    _cached_validator(schema).validate(args)
    if not isinstance(args, dict):
        raise RuntimeError("tool args JSON must be an object")
    return cast(dict[str, object], args)


def warm_tool_specs(settings: object | None = None) -> int:
    """Прогреть кэш валидаторов ВСЕХ tool-spec один раз на старте engine.

    Рендерит каждый ``*_TOOL_SPEC`` шаблон, строит и кэширует его jsonschema-валидатор (мета-валидация
    схемы — здесь, на старте, а не на первом tool-call) и заодно рано surface'ит битую схему/шаблон.
    Большинство спеков рендерятся без переменных; ``ingress_distill`` требует ``distill_max_chars`` —
    берём из ``settings`` (если переданы). Возвращает число успешно прогретых спеков; ошибки логируются
    (warning), но НЕ роняют engine — спек соберётся лениво на первом использовании.
    """
    distill_max_chars: int | None = None
    if settings is not None:
        ingress = getattr(settings, "ingress", None)
        distill_max_chars = getattr(ingress, "distill_max_chars", None)

    warmed = 0
    for path in PromptPath:
        if not path.name.endswith("_TOOL_SPEC"):
            continue
        jinja_vars: dict[str, object] = {}
        if path is PromptPath.INGRESS_DISTILL_TOOL_SPEC and distill_max_chars is not None:
            jinja_vars["distill_max_chars"] = distill_max_chars
        try:
            spec = load_tool_spec(path, **jinja_vars)
            _cached_validator(tool_spec_parameters(spec))
            warmed += 1
        except Exception as e:  # noqa: BLE001 — прогрев не должен валить старт; ленивый путь подстрахует
            log.warning("warm_tool_spec_failed", tool_spec=path.name, error=repr(e))
    log.info("warm_tool_specs_done", warmed=warmed, cached_validators=len(_VALIDATOR_CACHE))
    return warmed


__all__ = [
    "first_tool_call",
    "load_tool_spec",
    "tool_call_arguments_wire_from_tool_call",
    "tool_spec_parameters",
    "validate_tool_args_json",
    "warm_tool_specs",
]
