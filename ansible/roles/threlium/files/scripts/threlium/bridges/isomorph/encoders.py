"""Wire-энкодеры (§6.3). Схема владеется типами, не dict-литералами (docs/TYPES.md §106):

* OpenAI SSE/JSON — модели ``litellm.types.utils`` (``ModelResponse`` / ``ModelResponseStream`` / …);
* Anthropic SSE — типизированные VO кадров (:mod:`.anthropic_wire`);
* Anthropic JSON — ``litellm.types.utils.AnthropicMessagesResponse`` (TypedDict) + типизированные блоки;
* error-конверты — типизированные ``msgspec.Struct``.

``dict``/``json.dumps`` появляются **только** на крае HTTP-сериализации (``.model_dump()`` litellm-модели,
``msgspec.to_builtins`` VO), не как доменная модель.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator

import msgspec

from . import anthropic_wire, openai_wire
from .push_types import IsomorphBridgePushPayload
from .sse import SseFrame


def _chatcmpl_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex}"


def _call_id() -> str:
    return f"call_{uuid.uuid4().hex[:24]}"


def _toolu_id() -> str:
    return f"toolu_{uuid.uuid4().hex[:24]}"


def _anthropic_msg_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


def _created() -> int:
    return int(time.time())


def _usage(payload: IsomorphBridgePushPayload) -> openai_wire.OpenAIUsage:
    total = payload.usage.total or (payload.usage.prompt + payload.usage.completion)
    return openai_wire.OpenAIUsage(
        prompt_tokens=payload.usage.prompt, completion_tokens=payload.usage.completion,
        total_tokens=total)


def _openai_finish_reason(payload: IsomorphBridgePushPayload) -> str:
    if payload.tool_blocks:
        return "tool_calls"
    if payload.finish_reason in ("stop", "length", "tool_calls", "content_filter"):
        return payload.finish_reason
    return "stop"


def _anthropic_stop_reason(payload: IsomorphBridgePushPayload) -> str:
    if payload.tool_blocks:
        return "tool_use"
    return "max_tokens" if payload.finish_reason == "length" else "end_turn"


# ============================ OpenAI chat.completions ============================


def encode_openai_sse(payload: IsomorphBridgePushPayload) -> Iterator[SseFrame]:
    """``chat.completion.chunk`` через :mod:`.openai_wire`: role-в-первом, usage-чанк с
    пустым ``choices``, ``[DONE]``. tool_calls — одним фрагментом (фаза A)."""
    cid, created, model = _chatcmpl_id(), _created(), payload.model

    def chunk(*, delta: openai_wire.OpenAIDelta | None = None, finish_reason: str | None = None,
              choices: list | None = None, usage: openai_wire.OpenAIUsage | None = None) -> SseFrame:
        return openai_wire.chunk_frame(
            chunk_id=cid, created=created, model=model,
            delta=delta, finish_reason=finish_reason, choices=choices, usage=usage)

    yield chunk(delta=openai_wire.OpenAIDelta(role="assistant", content=""))
    if payload.text:
        yield chunk(delta=openai_wire.OpenAIDelta(content=payload.text))
    if payload.tool_blocks:
        tcs = [
            openai_wire.OpenAIDeltaToolCall(
                id=tb.id or _call_id(), index=i,
                function=openai_wire.OpenAIToolFunction(name=tb.name, arguments=tb.arguments),
            )
            for i, tb in enumerate(payload.tool_blocks)
        ]
        yield chunk(delta=openai_wire.OpenAIDelta(tool_calls=tcs))
    yield chunk(delta=openai_wire.OpenAIDelta(), finish_reason=_openai_finish_reason(payload))
    yield chunk(choices=[], usage=_usage(payload))
    yield SseFrame.done()


def encode_openai_json(payload: IsomorphBridgePushPayload) -> str:
    if payload.tool_blocks:
        message = openai_wire.OpenAIMessage(
            role="assistant", content=None,
            tool_calls=[
                openai_wire.OpenAIToolCall(
                    id=tb.id or _call_id(),
                    function=openai_wire.OpenAIToolFunction(name=tb.name, arguments=tb.arguments),
                )
                for tb in payload.tool_blocks
            ],
        )
    else:
        message = openai_wire.OpenAIMessage(role="assistant", content=payload.text)
    return openai_wire.completion_json(
        completion_id=_chatcmpl_id(), created=_created(), model=payload.model,
        message=message, finish_reason=_openai_finish_reason(payload), usage=_usage(payload))


# ============================ Anthropic Messages ============================


def encode_anthropic_sse(payload: IsomorphBridgePushPayload) -> Iterator[SseFrame]:
    """SSE-кадры из типизированных VO (:mod:`.anthropic_wire`)."""
    yield anthropic_wire.message_start(
        message_id=_anthropic_msg_id(), model=payload.model, input_tokens=payload.usage.prompt)

    index = 0
    if payload.text or not payload.tool_blocks:
        yield anthropic_wire.content_block_start_text(index)
        if payload.text:
            yield anthropic_wire.content_block_delta_text(index, text=payload.text)
        yield anthropic_wire.content_block_stop(index)
        index += 1

    for tb in payload.tool_blocks:
        yield anthropic_wire.content_block_start_tool(index, tool_id=tb.id or _toolu_id(), name=tb.name)
        yield anthropic_wire.content_block_delta_input_json(index, partial_json=tb.arguments or "{}")
        yield anthropic_wire.content_block_stop(index)
        index += 1

    yield anthropic_wire.message_delta(
        stop_reason=_anthropic_stop_reason(payload), output_tokens=payload.usage.completion)
    yield anthropic_wire.message_stop()


def encode_anthropic_json(payload: IsomorphBridgePushPayload) -> str:
    blocks: list[anthropic_wire.AnthropicTextBlock | anthropic_wire.AnthropicToolUseBlock] = []
    if payload.text or not payload.tool_blocks:
        blocks.append(anthropic_wire.AnthropicTextBlock(text=payload.text))
    for tb in payload.tool_blocks:
        try:
            inp = json.loads(tb.arguments) if tb.arguments else {}
        except json.JSONDecodeError:
            inp = {}
        blocks.append(anthropic_wire.AnthropicToolUseBlock(
            id=tb.id or _toolu_id(), name=tb.name, input=inp))
    msg = anthropic_wire.AnthropicMessage(
        id=_anthropic_msg_id(), model=payload.model, content=tuple(blocks),
        usage=anthropic_wire.AnthropicStreamUsage(
            input_tokens=payload.usage.prompt, output_tokens=payload.usage.completion),
        stop_reason=_anthropic_stop_reason(payload),
    )
    return msgspec.json.encode(msg).decode("utf-8")


# ============================ Error envelopes (типизированные VO) ============================


class _OpenAIError(msgspec.Struct, frozen=True):
    message: str
    type: str
    param: str | None = None
    code: str | None = None


class _OpenAIErrorEnvelope(msgspec.Struct, frozen=True):
    error: _OpenAIError


def openai_error_json(message: str, *, err_type: str = "api_error") -> str:
    return msgspec.json.encode(
        _OpenAIErrorEnvelope(error=_OpenAIError(message=message, type=err_type))
    ).decode("utf-8")


def anthropic_error_json(message: str, *, err_type: str = "api_error") -> str:
    return msgspec.json.encode(
        anthropic_wire._Error(error=anthropic_wire.AnthropicErrorBody(type=err_type, message=message))
    ).decode("utf-8")


def openai_error_sse(message: str, *, err_type: str = "api_error") -> SseFrame:
    return SseFrame.of_data(openai_error_json(message, err_type=err_type))


def anthropic_error_sse(message: str, *, err_type: str = "api_error") -> SseFrame:
    return anthropic_wire.error_event(err_type=err_type, message=message)
