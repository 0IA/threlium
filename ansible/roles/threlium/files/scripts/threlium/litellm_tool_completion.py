"""LiteLLM completion с ``tool_choice=required`` (reasoning sync / LightRAG async)."""
from __future__ import annotations

from typing import Any

from threlium.llm_wire import LlmChatResponse as ModelResponse

from threlium.litellm_client import litellm_acompletion, litellm_completion_sync
from threlium.litellm_tool_response import require_tool_calls_response
from threlium.litellm_wire import require_chat_model_response
from threlium.settings import ThreliumSettings
from threlium.types import (
    LITELLM_ACOMPLETION_PAYLOAD_KEYS,
    LiteLlmAcompletionKwargs,
    lite_llm_acompletion_to_dict,
)


def _tool_completion_payload(
    call: LiteLlmAcompletionKwargs,
    *,
    tools: list[dict[str, object]],
) -> dict[str, Any]:
    call_dict = lite_llm_acompletion_to_dict(call)
    call_dict["tools"] = tools
    call_dict["tool_choice"] = "required"
    return {
        k: v
        for k, v in call_dict.items()
        if k in LITELLM_ACOMPLETION_PAYLOAD_KEYS
    }


async def acompletion_required_tool(
    *,
    settings: ThreliumSettings,
    call: LiteLlmAcompletionKwargs,
    tools: list[dict[str, object]],
    correlation_override: dict[str, str] | None = None,
) -> ModelResponse:
    """Async completion; ответ должен содержать tool_calls."""
    payload = _tool_completion_payload(call, tools=tools)
    resp = require_chat_model_response(
        await litellm_acompletion(
            settings=settings,
            **payload,
            stream=False,
            correlation_override=correlation_override,
        )
    )
    require_tool_calls_response(resp, context="lightrag")
    return resp


def completion_required_tool_sync(
    *,
    settings: ThreliumSettings,
    call: LiteLlmAcompletionKwargs,
    tools: list[dict[str, object]],
    correlation_override: dict[str, str] | None = None,
) -> ModelResponse:
    """Sync completion для FSM reasoning."""
    payload = _tool_completion_payload(call, tools=tools)
    return require_chat_model_response(
        litellm_completion_sync(
            settings=settings,
            **payload,
            stream=False,
            correlation_override=correlation_override,
        )
    )


__all__ = [
    "acompletion_required_tool",
    "completion_required_tool_sync",
]
