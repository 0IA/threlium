"""Единая точка исходящего chat completion с ``tool_choice=required`` (один tool).

Контракт (см. ``docs/TYPES.md`` § tool bridge):

* ровно **один** tool на HTTP-вызов;
* ``X-Threlium-Call-Site`` корреляции = ``tools[0].function.name`` (гранулярная
  e2e-идентификация места вызова без инспекции тела);
* доменный разбор ответа — в ``*_tool_bridge`` модулях через
  :func:`~threlium.litellm_tool_response.require_single_tool_call`.

Сам ``litellm_completion`` / ``litellm_site_*`` из продуктового кода не вызывается —
только через этот модуль (reasoning multi-tool использует
:func:`correlation_with_call_site` + :func:`~threlium.litellm_tool_completion.completion_required_tool_sync`).
"""
from __future__ import annotations

from typing import cast

from litellm.types.utils import Message

from threlium.litellm_tool_completion import (
    acompletion_required_tool,
    completion_required_tool_sync,
)
from threlium.litellm_tool_response import require_tool_calls_response
from threlium.settings import ThreliumSettings, resolve_llm_endpoint
from threlium.types import (
    LiteLlmAcompletionKwargs,
    LiteLlmChatMessage,
    LitellmRoutingSite,
)
from threlium.types.litellm_correlation_header import LitellmCorrelationHeader


def tool_function_name(spec: dict[str, object]) -> str:
    """``function.name`` загруженного tool spec (валиден после ``load_tool_spec``)."""
    func = spec["function"]
    if not isinstance(func, dict):
        raise RuntimeError("tool spec: function must be an object")
    name = func.get("name")
    if not isinstance(name, str) or not name.strip():
        raise RuntimeError("tool spec: function.name must be a non-empty string")
    return name


def correlation_with_call_site(
    snap: dict[str, str] | None, call_site_wire: str
) -> dict[str, str] | None:
    """Копия снимка корреляции с переопределённым ``X-Threlium-Call-Site``.

    ``None`` (e2e-корреляция выключена) проходит насквозь — override не нужен.
    """
    if snap is None:
        return None
    out = dict(snap)
    out[LitellmCorrelationHeader.CALL_SITE.value] = call_site_wire
    return out


def build_site_call(
    settings: ThreliumSettings,
    site: LitellmRoutingSite,
    messages: list[LiteLlmChatMessage],
) -> LiteLlmAcompletionKwargs:
    """``LiteLlmAcompletionKwargs`` из записи каталога ``settings.litellm`` для *site*."""
    ep = resolve_llm_endpoint(settings.litellm, site)
    mr = ep.max_retries if ep.max_retries is not None else settings.litellm.max_retries
    return LiteLlmAcompletionKwargs(
        model=ep.model,
        messages=list(messages),
        timeout=float(ep.timeout),
        max_retries=mr,
        api_key=ep.api_key,
        api_base=ep.api_base,
        max_tokens=ep.max_tokens,
        chat_template_kwargs=ep.chat_template_kwargs or None,
    )


def invoke_required_tool(
    *,
    settings: ThreliumSettings,
    call: LiteLlmAcompletionKwargs,
    tool_spec: dict[str, object],
    correlation_snap: dict[str, str] | None,
    context: str,
) -> Message:
    """Sync: один tool, ``call_site=function.name``; вернуть assistant с tool_call."""
    call_site = tool_function_name(tool_spec)
    corr = correlation_with_call_site(correlation_snap, call_site)
    resp = completion_required_tool_sync(
        settings=settings,
        call=call,
        tools=[tool_spec],
        correlation_override=corr,
    )
    return require_tool_calls_response(resp, context=context)


async def ainvoke_required_tool(
    *,
    settings: ThreliumSettings,
    call: LiteLlmAcompletionKwargs,
    tool_spec: dict[str, object],
    correlation_snap: dict[str, str] | None,
    context: str,
) -> Message:
    """Async: один tool, ``call_site=function.name``; вернуть assistant с tool_call."""
    call_site = tool_function_name(tool_spec)
    corr = correlation_with_call_site(correlation_snap, call_site)
    resp = await acompletion_required_tool(
        settings=settings,
        call=call,
        tools=[tool_spec],
        correlation_override=corr,
    )
    msg = resp.choices[0].message
    if msg is None:
        raise RuntimeError(f"{context}: empty assistant message")
    return cast(Message, msg)


__all__ = [
    "ainvoke_required_tool",
    "build_site_call",
    "correlation_with_call_site",
    "invoke_required_tool",
    "tool_function_name",
]
