"""Типизированные VO исходящего OpenAI ``chat.completions`` wire (схема владеется msgspec.Struct,
не dict-литералами и не litellm-моделями).

Зеркало :mod:`.anthropic_wire` для OpenAI-поверхности моста ``isomorph``: stream-чанки
(``chat.completion.chunk``) и не-стрим ответ (``chat.completion``). Сериализация — ``msgspec.json``;
SSE-грамматика — только в :class:`.sse.SseFrame`. ``object`` всегда на проводе (required-поле,
``omit_defaults`` его не вырезает); ``usage``/пустые delta-поля опускаются.
"""
from __future__ import annotations

import msgspec

from .sse import SseFrame

_ENC = msgspec.json.Encoder()


def _json(payload: msgspec.Struct) -> str:
    return _ENC.encode(payload).decode("utf-8")


# --- общие блоки ----------------------------------------------------------------------------


class OpenAIToolFunction(msgspec.Struct, frozen=True):
    name: str
    arguments: str


class OpenAIDeltaToolCall(msgspec.Struct, frozen=True):
    index: int
    id: str
    function: OpenAIToolFunction
    type: str = "function"


class OpenAIToolCall(msgspec.Struct, frozen=True):
    id: str
    function: OpenAIToolFunction
    type: str = "function"


class OpenAIUsage(msgspec.Struct, frozen=True):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


# --- stream (chat.completion.chunk) ---------------------------------------------------------


class OpenAIDelta(msgspec.Struct, frozen=True, omit_defaults=True):
    role: str | None = None
    content: str | None = None
    tool_calls: list[OpenAIDeltaToolCall] | None = None


class OpenAIChunkChoice(msgspec.Struct, frozen=True, omit_defaults=True):
    index: int = 0
    delta: OpenAIDelta = msgspec.field(default_factory=OpenAIDelta)
    finish_reason: str | None = None


class OpenAIChatChunk(msgspec.Struct, frozen=True, omit_defaults=True):
    id: str
    created: int
    model: str
    object: str  # always "chat.completion.chunk" (required → всегда на проводе)
    choices: list[OpenAIChunkChoice]
    usage: OpenAIUsage | None = None


# --- non-stream (chat.completion) -----------------------------------------------------------


class OpenAIMessage(msgspec.Struct, frozen=True, omit_defaults=True):
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[OpenAIToolCall] | None = None


class OpenAIChoice(msgspec.Struct, frozen=True):
    index: int
    message: OpenAIMessage
    finish_reason: str


class OpenAIChatCompletion(msgspec.Struct, frozen=True, omit_defaults=True):
    id: str
    created: int
    model: str
    object: str  # "chat.completion"
    choices: list[OpenAIChoice]
    usage: OpenAIUsage | None = None


# --- билдеры ---------------------------------------------------------------------------------


def chunk_frame(
    *,
    chunk_id: str,
    created: int,
    model: str,
    delta: OpenAIDelta | None = None,
    finish_reason: str | None = None,
    choices: list[OpenAIChunkChoice] | None = None,
    usage: OpenAIUsage | None = None,
) -> SseFrame:
    c = OpenAIChatChunk(
        id=chunk_id,
        created=created,
        model=model,
        object="chat.completion.chunk",
        choices=choices if choices is not None
        else [OpenAIChunkChoice(index=0, delta=delta or OpenAIDelta(), finish_reason=finish_reason)],
        usage=usage,
    )
    return SseFrame.of_data(_json(c))


def completion_json(
    *,
    completion_id: str,
    created: int,
    model: str,
    message: OpenAIMessage,
    finish_reason: str,
    usage: OpenAIUsage,
) -> str:
    return _json(OpenAIChatCompletion(
        id=completion_id,
        created=created,
        model=model,
        object="chat.completion",
        choices=[OpenAIChoice(index=0, message=message, finish_reason=finish_reason)],
        usage=usage,
    ))
