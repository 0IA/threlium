"""Валидация LiteLLM chat completion с ``tool_choice=required``."""
from __future__ import annotations

from threlium.llm_wire import LlmToolCall as ChatCompletionMessageToolCall, LlmAssistantMessage as Message, LlmChatResponse as ModelResponse


class LiteLlmToolResponseError(RuntimeError):
    """Ответ completion не соответствует контракту tool_calls."""


def litellm_choice_finish_reason(resp: ModelResponse) -> str | None:
    """``finish_reason`` первого choice (или None)."""
    choices = resp.choices or []
    if not choices:
        return None
    fr = choices[0].finish_reason
    if fr is None:
        return None
    return str(fr)


def require_single_tool_call(
    msg: Message,
    *,
    context: str,
) -> ChatCompletionMessageToolCall:
    """Ровно один tool_call в assistant message."""
    tcs = msg.tool_calls
    if not tcs:
        raise LiteLlmToolResponseError(
            f"{context}: missing tool_calls in assistant message"
        )
    if len(tcs) != 1:
        raise LiteLlmToolResponseError(
            f"{context}: expected exactly one tool_call, got {len(tcs)}"
        )
    return tcs[0]


def require_tool_calls_message(
    msg: Message,
    *,
    context: str,
    finish_reason: str | None = None,
) -> Message:
    """Assistant message с одним tool_call и ``finish_reason=tool_calls``."""
    if finish_reason is not None and finish_reason != "tool_calls":
        raise LiteLlmToolResponseError(
            f"{context}: expected finish_reason=tool_calls, got {finish_reason!r}"
        )
    require_single_tool_call(msg, context=context)
    return msg


def require_tool_calls_response(
    resp: ModelResponse,
    *,
    context: str,
) -> Message:
    """Первый choice: ``finish_reason=tool_calls`` + ровно один tool_call."""
    choices = resp.choices or []
    if not choices:
        raise LiteLlmToolResponseError(f"{context}: empty litellm choices")
    ch0 = choices[0]
    msg = ch0.message
    if msg is None:
        raise LiteLlmToolResponseError(f"{context}: litellm choice without message")
    fr = ch0.finish_reason
    finish_wire = str(fr) if fr is not None else None
    return require_tool_calls_message(
        msg,
        context=context,
        finish_reason=finish_wire,
    )


__all__ = [
    "LiteLlmToolResponseError",
    "litellm_choice_finish_reason",
    "require_single_tool_call",
    "require_tool_calls_message",
    "require_tool_calls_response",
]
