"""LiteLLM adapters: llm_func / embedding_func / rerank_func builders for LightRAG."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np
from lightrag.types import GPTKeywordExtractionFormat
from litellm.types.utils import Embedding, Message

from threlium.litellm_client import litellm_acompletion, litellm_aembedding, litellm_arerank
from threlium.litellm_wire import require_chat_model_response, require_embedding_response
from threlium.settings import (
    ThreliumSettings,
    LlmEndpoint,
    EmbeddingEndpoint,
    RerankEndpoint,
)
from threlium.types import (
    LightragLiteLlmCompletionBody,
    LitellmCallSite,
    LiteLlmAcompletionKwargs,
    LiteLlmArerankKwargs,
    LiteLlmAembeddingKwargs,
    LiteLlmChatMessage,
    lite_llm_acompletion_to_dict,
    lite_llm_aembedding_to_dict,
    lite_llm_arerank_to_dict,
)
from threlium.types.litellm_correlation_header import LitellmCorrelationHeader

from threlium.logutil import logger

log = logger.bind(stage="lightrag")

_LITELLM_ACOMPLETION_PAYLOAD_KEYS = frozenset(
    {
        "model",
        "messages",
        "timeout",
        "max_retries",
        "api_key",
        "api_base",
        "max_tokens",
        "tools",
        "tool_choice",
        "response_format",
        "extra_headers",
        "chat_template_kwargs",
    }
)


def _detect_lightrag_phase(
    base_call_site: str | None,
    *,
    keyword_extraction: bool,
    has_history: bool,
    has_system_prompt: bool,
) -> str:
    """Гранулярный ``X-Threlium-Call-Site`` по сигналам ``llm_func`` без инспекции prompt content.

    Сигналы (из исходников ``lightrag.operate`` / ``lightrag.utils.use_llm_func_with_cache``):

    * **keyword_extraction=True** — только ``get_keywords_from_query`` (query path).
    * **history_messages non-empty** — gleaning (continue entity extraction, index path).
    * **system_prompt absent** — ``_handle_single_entity_summary`` (index, ``_priority=8``).
    * **остальное** — entity extraction (index) или rag/kg response (query).
    """
    if base_call_site == LitellmCallSite.LIGHTRAG_QUERY.value:
        if keyword_extraction:
            return LitellmCallSite.LIGHTRAG_QUERY_KEYWORDS.value
        return LitellmCallSite.LIGHTRAG_QUERY_RESPONSE.value

    if keyword_extraction:
        return LitellmCallSite.LIGHTRAG_QUERY_KEYWORDS.value

    if has_history:
        return LitellmCallSite.LIGHTRAG_INDEX_GLEANING.value
    if not has_system_prompt:
        return LitellmCallSite.LIGHTRAG_INDEX_SUMMARIZE.value
    return LitellmCallSite.LIGHTRAG_INDEX_ENTITY.value


def _llm_bridge_completion_text(
    msg_obj: Message | None,
    *,
    keyword_extraction: bool,
) -> str:
    if msg_obj is None:
        if keyword_extraction:
            log.warning("llm_func_empty_message_keyword_extraction")
            raise RuntimeError(
                "LightRAG LLM bridge: empty message in keyword extraction response"
            )
        return ""

    if keyword_extraction:
        raw_c = msg_obj.content
        if not isinstance(raw_c, str) or not raw_c.strip():
            log.warning("llm_func_no_parsed_content", content_type=type(raw_c).__name__)
            raise RuntimeError(
                "LightRAG LLM bridge: keyword extraction response missing text content"
            )
        try:
            parsed = GPTKeywordExtractionFormat.model_validate_json(raw_c)
            return str(parsed.model_dump_json())
        except Exception as exc:
            log.warning(
                "keyword_extraction_parse_failed",
                exc_type=type(exc).__name__,
                exc_msg=str(exc),
            )
            raise

    raw_c = msg_obj.content
    if isinstance(raw_c, str):
        return str(LightragLiteLlmCompletionBody.parse(raw_c).value)
    log.warning("llm_func_unexpected_content_type", content_type=type(raw_c).__name__)
    return ""


def build_llm_func(
    settings: ThreliumSettings,
    *,
    llm_ep: LlmEndpoint,
    default_max_retries: int,
    max_tokens: int | None = None,
    chat_template_kwargs: dict[str, Any] | None = None,
) -> Callable[..., Awaitable[str]]:
    closure_max_tokens = max_tokens
    closure_ctk = chat_template_kwargs
    llm_timeout = float(llm_ep.timeout)

    async def llm_func(
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list[dict] | None = None,
        keyword_extraction: bool = False,
        max_tokens: int | None = None,
        hashing_kv: object | None = None,
        _priority: int = 10,
        enable_cot: bool = False,
        stream: bool | None = None,
        **kwargs: Any,
    ) -> str:
        correlation: dict[str, str] | None = kwargs.pop(
            "_threlium_e2e_correlation", None
        )
        if correlation is not None:
            base_cs = correlation.get(LitellmCorrelationHeader.CALL_SITE.value)
            granular_cs = _detect_lightrag_phase(
                base_cs,
                keyword_extraction=keyword_extraction,
                has_history=bool(history_messages),
                has_system_prompt=bool(system_prompt),
            )
            correlation[LitellmCorrelationHeader.CALL_SITE.value] = granular_cs

        unsupported: list[str] = []
        if stream is True:
            unsupported.append("stream=True(ignored)")
        if enable_cot:
            unsupported.append("enable_cot=True(no-op)")
        if kwargs:
            unsupported.append(f"unknown_kwargs={sorted(kwargs.keys())}")
        if unsupported:
            log.debug("llm_func_unsupported_args", args=unsupported)

        effective_max = max_tokens if max_tokens is not None else closure_max_tokens

        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for m in history_messages or []:
            messages.append(m)
        messages.append({"role": "user", "content": prompt})
        litellm_messages = [
            LiteLlmChatMessage(role=str(m["role"]), content=str(m["content"]))
            for m in messages
        ]
        response_format: object | None = (
            GPTKeywordExtractionFormat if keyword_extraction else None
        )
        mr = llm_ep.max_retries if llm_ep.max_retries is not None else default_max_retries
        call = LiteLlmAcompletionKwargs(
            model=llm_ep.model,
            messages=litellm_messages,
            timeout=llm_timeout,
            max_retries=mr,
            api_key=llm_ep.api_key,
            api_base=llm_ep.api_base,
            max_tokens=effective_max,
            response_format=response_format,
            chat_template_kwargs=closure_ctk,
        )
        call_kwargs = lite_llm_acompletion_to_dict(call)
        litellm_payload: dict[str, Any] = {
            k: v
            for k, v in call_kwargs.items()
            if k in _LITELLM_ACOMPLETION_PAYLOAD_KEYS
        }
        resp = require_chat_model_response(
            await litellm_acompletion(
                settings=settings,
                **litellm_payload,
                stream=False,
                correlation_override=correlation,
            )
        )
        choice = resp.choices[0]
        msg_obj: Message | None = choice.message
        return _llm_bridge_completion_text(
            msg_obj,
            keyword_extraction=keyword_extraction,
        )

    return llm_func


def build_embedding_func(
    settings: ThreliumSettings,
    *,
    embed_ep: EmbeddingEndpoint,
    default_max_retries: int,
):
    mr_def = default_max_retries

    async def embed_func(texts: list[str], **_kwargs: Any):
        correlation: dict[str, str] | None = _kwargs.pop(
            "_threlium_e2e_correlation", None
        )
        mr = embed_ep.max_retries if embed_ep.max_retries is not None else mr_def
        call = LiteLlmAembeddingKwargs(
            model=embed_ep.model,
            embedding_input=texts,
            timeout=float(embed_ep.timeout),
            max_retries=mr,
            api_key=embed_ep.api_key,
            api_base=embed_ep.api_base,
            encoding_format=embed_ep.encoding_format,
        )
        call_kwargs = lite_llm_aembedding_to_dict(call)
        resp = require_embedding_response(
            await litellm_aembedding(settings=settings, **call_kwargs, correlation_override=correlation)
        )
        data: list[Embedding] = list(resp.data or [])
        return np.array([item.embedding for item in data], dtype=np.float32)

    return embed_func


def build_rerank_func(
    settings: ThreliumSettings,
    *,
    rerank_ep: RerankEndpoint,
    default_max_retries: int,
) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
    mr_def = default_max_retries

    async def rerank_func(
        query: str,
        documents: list[str],
        top_n: int | None = None,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        correlation: dict[str, str] | None = _kwargs.pop(
            "_threlium_e2e_correlation", None
        )
        mr = rerank_ep.max_retries if rerank_ep.max_retries is not None else mr_def
        effective_top_n = top_n if top_n is not None else rerank_ep.top_n
        call = LiteLlmArerankKwargs(
            model=rerank_ep.model,
            query=query,
            documents=documents,
            timeout=float(rerank_ep.timeout),
            max_retries=mr,
            api_key=rerank_ep.api_key,
            api_base=rerank_ep.api_base,
            top_n=effective_top_n,
            custom_llm_provider="hosted_vllm",
        )
        call_kwargs = lite_llm_arerank_to_dict(call)
        resp = await litellm_arerank(
            settings=settings,
            **call_kwargs,
            correlation_override=correlation,
        )
        return [
            {"index": r["index"], "relevance_score": r["relevance_score"]}
            for r in (resp.results or [])
        ]

    return rerank_func
