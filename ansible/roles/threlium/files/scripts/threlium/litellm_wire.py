"""Узкая граница типов ответов LLM (наш OpenAI-совместимый клиент, без litellm).

Клиент (:mod:`threlium.openai_compatible_client`) уже декодирует ответ в типизированные
msgspec-VO (:mod:`threlium.llm_wire`), поэтому здесь — только сужение типа (assert) для
вызовов ``stream=False`` (chat) и embeddings; форма совпадает с e2e CustomLLM-стабами.
"""
from __future__ import annotations

from threlium.llm_wire import LlmChatResponse, LlmEmbeddingResponse


def require_chat_model_response(resp: object) -> LlmChatResponse:
    """Сузить ответ chat completion до :class:`LlmChatResponse`."""
    if isinstance(resp, LlmChatResponse):
        return resp
    raise TypeError(f"expected LlmChatResponse; got {type(resp).__name__!r}")


def require_embedding_response(resp: object) -> LlmEmbeddingResponse:
    """Сузить ответ embedding до :class:`LlmEmbeddingResponse`."""
    if isinstance(resp, LlmEmbeddingResponse):
        return resp
    raise TypeError(f"expected LlmEmbeddingResponse; got {type(resp).__name__!r}")
