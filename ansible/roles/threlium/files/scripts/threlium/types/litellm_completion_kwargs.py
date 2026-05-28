"""Сборка kwargs для ``litellm.completion`` / ``acompletion`` / ``aembedding`` (граница вызова)."""
from __future__ import annotations

from typing import Any

import msgspec


class LiteLlmChatMessage(msgspec.Struct, frozen=True):
    """Один элемент ``messages`` для chat completion."""

    role: str
    content: str


class LiteLlmAcompletionKwargs(msgspec.Struct, frozen=True):
    """Поля для ``litellm.acompletion`` / ``completion`` без ``stream``."""

    model: str
    messages: list[LiteLlmChatMessage]
    timeout: float
    max_retries: int = 0
    api_key: str | None = None
    api_base: str | None = None
    max_tokens: int | None = None
    thinking_token_budget: int | None = None
    tools: list[dict[str, object]] | None = None
    tool_choice: str | None = None
    response_format: object | None = None
    chat_template_kwargs: dict[str, Any] | None = None


def lite_llm_acompletion_to_dict(k: LiteLlmAcompletionKwargs) -> dict[str, object]:
    """Сборка dict для LiteLLM; ``response_format`` может быть классом Pydantic (не через ``to_builtins``)."""
    out: dict[str, object] = {
        "model": k.model,
        "messages": [{"role": m.role, "content": m.content} for m in k.messages],
        "timeout": k.timeout,
        "max_retries": k.max_retries,
    }
    if k.api_key is not None:
        out["api_key"] = k.api_key
    if k.api_base is not None:
        out["api_base"] = k.api_base
    if k.max_tokens is not None:
        out["max_tokens"] = k.max_tokens
    if k.thinking_token_budget is not None:
        out["thinking_token_budget"] = k.thinking_token_budget
    if k.tools is not None:
        out["tools"] = k.tools
    if k.tool_choice is not None:
        out["tool_choice"] = k.tool_choice
    if k.response_format is not None:
        out["response_format"] = k.response_format
    if k.chat_template_kwargs:
        out["chat_template_kwargs"] = k.chat_template_kwargs
    return out


class LiteLlmAembeddingKwargs(msgspec.Struct, frozen=True):
    """Поля для ``litellm.aembedding``."""

    model: str
    embedding_input: list[str] = msgspec.field(name="input")
    timeout: float
    max_retries: int = 0
    api_key: str | None = None
    api_base: str | None = None
    encoding_format: str | None = None


def lite_llm_aembedding_to_dict(k: LiteLlmAembeddingKwargs) -> dict[str, object]:
    out: dict[str, object] = {
        "model": k.model,
        "input": k.embedding_input,
        "timeout": k.timeout,
        "max_retries": k.max_retries,
    }
    if k.api_key is not None:
        out["api_key"] = k.api_key
    if k.api_base is not None:
        out["api_base"] = k.api_base
    if k.encoding_format is not None and str(k.encoding_format).strip() != "":
        out["encoding_format"] = k.encoding_format
    return out


class LiteLlmArerankKwargs(msgspec.Struct, frozen=True):
    """Поля для ``litellm.arerank``."""

    model: str
    query: str
    documents: list[str]
    timeout: float
    max_retries: int = 0
    api_key: str | None = None
    api_base: str | None = None
    top_n: int | None = None
    custom_llm_provider: str | None = None


def lite_llm_arerank_to_dict(k: LiteLlmArerankKwargs) -> dict[str, object]:
    out: dict[str, object] = {
        "model": k.model,
        "query": k.query,
        "documents": k.documents,
        "timeout": k.timeout,
        "max_retries": k.max_retries,
    }
    if k.api_key is not None:
        out["api_key"] = k.api_key
    if k.api_base is not None:
        out["api_base"] = k.api_base
    if k.top_n is not None:
        out["top_n"] = k.top_n
    if k.custom_llm_provider is not None:
        out["custom_llm_provider"] = k.custom_llm_provider
    return out
