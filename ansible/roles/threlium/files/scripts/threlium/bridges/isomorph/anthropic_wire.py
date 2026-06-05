"""Типизированные VO кадров Anthropic Messages SSE (схема владеется именованными msgspec.Struct,
не dict-литералами).

Anthropic-фрейминг ручной (план §6.3: litellm не владеет outbound Anthropic SSE-событиями, а
`anthropic` SDK не зависимость) — но **схема каждого кадра** инкапсулирована в VO, сериализация JSON —
`msgspec.json.encode` (UTF-8, без ascii-эскейпа). Билдеры отдают типизированный :class:`.sse.SseFrame`;
SSE-грамматика (``event:``/``data:``/``\\n\\n``) — только в ``SseFrame.render()``.

Не-стрим Anthropic `Message` использует `litellm.types.utils.AnthropicMessagesResponse` (TypedDict),
OpenAI-форма — `litellm.types.utils` модели (см. ``encoders.py``).
"""
from __future__ import annotations

import msgspec

from .sse import SseFrame

_ENC = msgspec.json.Encoder()


def _sse(event: str, payload: msgspec.Struct) -> SseFrame:
    return SseFrame.of_event(event, _ENC.encode(payload).decode("utf-8"))


# --- usage / content blocks / deltas -----------------------------------------------------


class AnthropicStreamUsage(msgspec.Struct, frozen=True):
    input_tokens: int = 0
    output_tokens: int = 0


class AnthropicTextBlock(msgspec.Struct, frozen=True):
    text: str = ""
    type: str = "text"


class AnthropicToolUseBlock(msgspec.Struct, frozen=True):
    id: str = ""
    name: str = ""
    input: dict = msgspec.field(default_factory=dict)
    type: str = "tool_use"


class AnthropicTextDelta(msgspec.Struct, frozen=True):
    text: str = ""
    type: str = "text_delta"


class AnthropicInputJsonDelta(msgspec.Struct, frozen=True):
    partial_json: str = ""
    type: str = "input_json_delta"


class AnthropicMessageStartMessage(msgspec.Struct, frozen=True):
    id: str
    model: str
    usage: AnthropicStreamUsage
    type: str = "message"
    role: str = "assistant"
    content: tuple = ()
    stop_reason: str | None = None
    stop_sequence: str | None = None


class AnthropicMessageDeltaBody(msgspec.Struct, frozen=True):
    stop_reason: str | None = None
    stop_sequence: str | None = None


class AnthropicErrorBody(msgspec.Struct, frozen=True):
    type: str
    message: str


class AnthropicMessage(msgspec.Struct, frozen=True):
    """Не-стрим Anthropic ``Message`` (типизированный VO; схема владеется типом, не TypedDict-dict)."""

    id: str
    model: str
    content: tuple[AnthropicTextBlock | AnthropicToolUseBlock, ...]
    usage: AnthropicStreamUsage
    stop_reason: str
    type: str = "message"
    role: str = "assistant"
    stop_sequence: str | None = None


# --- кадры событий -----------------------------------------------------------------------


class _MessageStart(msgspec.Struct, frozen=True):
    message: AnthropicMessageStartMessage
    type: str = "message_start"


class _ContentBlockStart(msgspec.Struct, frozen=True):
    index: int
    content_block: AnthropicTextBlock | AnthropicToolUseBlock
    type: str = "content_block_start"


class _ContentBlockDelta(msgspec.Struct, frozen=True):
    index: int
    delta: AnthropicTextDelta | AnthropicInputJsonDelta
    type: str = "content_block_delta"


class _ContentBlockStop(msgspec.Struct, frozen=True):
    index: int
    type: str = "content_block_stop"


class _MessageDelta(msgspec.Struct, frozen=True):
    delta: AnthropicMessageDeltaBody
    usage: AnthropicStreamUsage
    type: str = "message_delta"


class _MessageStop(msgspec.Struct, frozen=True):
    type: str = "message_stop"


class _Error(msgspec.Struct, frozen=True):
    error: AnthropicErrorBody
    type: str = "error"


# --- публичные билдеры SSE-строк ----------------------------------------------------------


def message_start(*, message_id: str, model: str, input_tokens: int) -> SseFrame:
    return _sse("message_start", _MessageStart(
        message=AnthropicMessageStartMessage(
            id=message_id, model=model,
            usage=AnthropicStreamUsage(input_tokens=input_tokens, output_tokens=1),
        ),
    ))


def content_block_start_text(index: int) -> SseFrame:
    return _sse("content_block_start", _ContentBlockStart(index=index, content_block=AnthropicTextBlock()))


def content_block_start_tool(index: int, *, tool_id: str, name: str) -> SseFrame:
    return _sse("content_block_start", _ContentBlockStart(
        index=index, content_block=AnthropicToolUseBlock(id=tool_id, name=name)))


def content_block_delta_text(index: int, *, text: str) -> SseFrame:
    return _sse("content_block_delta", _ContentBlockDelta(index=index, delta=AnthropicTextDelta(text=text)))


def content_block_delta_input_json(index: int, *, partial_json: str) -> SseFrame:
    return _sse("content_block_delta", _ContentBlockDelta(
        index=index, delta=AnthropicInputJsonDelta(partial_json=partial_json)))


def content_block_stop(index: int) -> SseFrame:
    return _sse("content_block_stop", _ContentBlockStop(index=index))


def message_delta(*, stop_reason: str, output_tokens: int) -> SseFrame:
    return _sse("message_delta", _MessageDelta(
        delta=AnthropicMessageDeltaBody(stop_reason=stop_reason, stop_sequence=None),
        usage=AnthropicStreamUsage(output_tokens=output_tokens),
    ))


def message_stop() -> SseFrame:
    return _sse("message_stop", _MessageStop())


def error_event(*, err_type: str, message: str) -> SseFrame:
    return _sse("error", _Error(error=AnthropicErrorBody(type=err_type, message=message)))
