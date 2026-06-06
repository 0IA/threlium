"""Лёгкие msgspec-VO ответов OpenAI-совместимого LLM (наш клиент вместо litellm).

Толерантный декод (лишние поля игнорируются ``msgspec`` по умолчанию). Форма зеркалит
OpenAI/litellm: ``choices[0].message.{content,tool_calls}``,
``tool_calls[].function.{name,arguments}`` — чтобы потребители (reasoning, tool-bridges,
litellm_tool_response) меняли только источник импорта, а не доступ к полям.

Зависимость — только ``msgspec`` (модуль грузится в горячем пути; без litellm).
"""
from __future__ import annotations

import msgspec


class LlmToolFunction(msgspec.Struct, frozen=True):
    """``function`` внутри tool_call: имя + JSON-строка аргументов."""

    name: str | None = None
    arguments: str = ""


class LlmToolCall(msgspec.Struct, frozen=True):
    """Один ``tool_call`` ассистента (форма OpenAI: ``id``/``type``/``function``)."""

    function: LlmToolFunction
    id: str | None = None
    type: str = "function"


class LlmAssistantMessage(msgspec.Struct, frozen=True):
    """``choices[].message`` — ассистентское сообщение (текст и/или tool_calls)."""

    role: str = "assistant"
    content: str | None = None
    tool_calls: list[LlmToolCall] | None = None


class LlmUsage(msgspec.Struct, frozen=True):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LlmChoice(msgspec.Struct, frozen=True):
    message: LlmAssistantMessage
    index: int = 0
    finish_reason: str | None = None


class LlmChatResponse(msgspec.Struct, frozen=True):
    """Ответ ``/chat/completions`` (не-stream)."""

    choices: list[LlmChoice] = msgspec.field(default_factory=list)
    id: str = ""
    model: str = ""
    usage: LlmUsage | None = None


class LlmEmbedding(msgspec.Struct, frozen=True):
    embedding: list[float]
    index: int = 0


class LlmEmbeddingResponse(msgspec.Struct, frozen=True):
    """Ответ ``/embeddings``."""

    data: list[LlmEmbedding] = msgspec.field(default_factory=list)
    model: str = ""


class LlmRerankResult(msgspec.Struct, frozen=True):
    index: int
    relevance_score: float


class LlmRerankResponse(msgspec.Struct, frozen=True):
    """Ответ vLLM ``/rerank``: список ``{index, relevance_score}``."""

    results: list[LlmRerankResult] = msgspec.field(default_factory=list)


_CHAT_DECODER = msgspec.json.Decoder(LlmChatResponse)
_EMBED_DECODER = msgspec.json.Decoder(LlmEmbeddingResponse)
_RERANK_DECODER = msgspec.json.Decoder(LlmRerankResponse)


def decode_chat_response(raw: bytes) -> LlmChatResponse:
    return _CHAT_DECODER.decode(raw)


def decode_embedding_response(raw: bytes) -> LlmEmbeddingResponse:
    return _EMBED_DECODER.decode(raw)


def decode_rerank_response(raw: bytes) -> LlmRerankResponse:
    return _RERANK_DECODER.decode(raw)
