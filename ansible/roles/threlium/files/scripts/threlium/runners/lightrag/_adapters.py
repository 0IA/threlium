"""LiteLLM adapters: llm_func / embedding_func / rerank_func builders for LightRAG."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import jsonschema
import numpy as np
from threlium.llm_wire import LlmEmbedding as Embedding

from threlium.litellm_client import litellm_aembedding, litellm_arerank
from threlium.litellm_route_context import get_litellm_correlation_from_ctxvar
from threlium.litellm_required_tool import (
    ainvoke_required_tool,
    ainvoke_with_bridge_retries,
    build_site_call,
    correlation_with_call_site,
)
from threlium.litellm_tool_response import LiteLlmToolResponseError
from threlium.litellm_tool_spec import load_tool_spec
from threlium.litellm_wire import require_embedding_response
from threlium.settings import (
    ThreliumSettings,
    LlmEndpoint,
    EmbeddingEndpoint,
    RerankEndpoint,
)
from threlium.types import (
    LitellmCallSite,
    LiteLlmArerankKwargs,
    LiteLlmAembeddingKwargs,
    LiteLlmChatMessage,
    lite_llm_aembedding_to_dict,
    lite_llm_arerank_to_dict,
)
from threlium.types.lightrag_tool_function import LightragToolBridgeError
from threlium.types.lightrag_tool_phase import lightrag_tool_phase_for_call_site

from threlium.logutil import logger

from .lightrag_tool_bridge import (
    parse_tool_call_for_phase,
    struct_to_lightrag_wire,
    to_lightrag_return_value,
)

log = logger.bind(stage="lightrag")

_MAX_LIGHTRAG_TOOL_BRIDGE_RETRIES = 2

# kwarg-мост корреляции к предсозданным воркерам lightrag (см. embed_func и
# _construction._install_query_correlation_bridge). Имя приватное, lightrag его не трогает —
# он лишь прокидывает kwargs через очередь от submission-границы к воркеру.
LIGHTRAG_CORRELATION_KWARG = "_threlium_e2e_correlation"


# Резолвер call-site точки вызова LightRAG: сигналы запроса → call-site. call-site ВСЕГДА известен.
CallSiteResolver = Callable[..., LitellmCallSite]


def fixed_call_site(call_site: LitellmCallSite) -> CallSiteResolver:
    """Точка 1:1 с фазой — резолвер-константа (keyword → extract_query_keywords, query → generate_rag_answer)."""

    def _resolve(**_signals: bool) -> LitellmCallSite:
        return call_site

    return _resolve


def extract_call_site(*, has_history: bool, has_system_prompt: bool) -> LitellmCallSite:
    """Роль ``extract`` LightRAG (``role_llm_funcs["extract"]``: entity-pass + gleaning + summarize в одной
    функции) — ЕДИНСТВЕННАЯ точка, где call-site зависит от структуры: gleaning-continue несёт history;
    summarize идёт без system_prompt; иначе первичный entity-pass."""
    if has_history:
        return LitellmCallSite.EXTRACT_KNOWLEDGE_GRAPH_GLEANING
    if not has_system_prompt:
        return LitellmCallSite.SUMMARIZE_DESCRIPTIONS
    return LitellmCallSite.EXTRACT_KNOWLEDGE_GRAPH


def _build_chat_messages(
    prompt: str,
    system_prompt: str | None,
    history_messages: list[dict] | None,
) -> list[LiteLlmChatMessage]:
    """``system?`` + ``history*`` + ``user(prompt)`` → litellm chat-сообщения."""
    raw: list[dict] = []
    if system_prompt:
        raw.append({"role": "system", "content": system_prompt})
    raw.extend(history_messages or [])
    raw.append({"role": "user", "content": prompt})
    return [LiteLlmChatMessage(role=str(m["role"]), content=str(m["content"])) for m in raw]


def _log_unsupported_llm_args(*, stream: bool | None, enable_cot: bool, extra: dict[str, Any]) -> None:
    """Залогировать игнорируемые LightRAG-аргументы (stream / enable_cot / неизвестные kwargs)."""
    unsupported: list[str] = []
    if stream is True:
        unsupported.append("stream=True(ignored)")
    if enable_cot:
        unsupported.append("enable_cot=True(no-op)")
    if extra:
        unsupported.append(f"unknown_kwargs={sorted(extra.keys())}")
    if unsupported:
        log.debug("llm_func_unsupported_args", args=unsupported)


def build_llm_func(
    settings: ThreliumSettings,
    *,
    llm_ep: LlmEndpoint,
    default_max_retries: int,
    chat_template_kwargs: dict[str, Any] | None = None,
    resolve_call_site: CallSiteResolver,
) -> Callable[..., Awaitable[str]]:
    """LLM-функция LightRAG для ОДНОЙ точки вызова (``role_llm_configs``).

    ``resolve_call_site`` детерминирует call-site (константа для keyword/query, структурный резолвер для
    ``extract``) — call-site ВСЕГДА известен, без if-fallback и без сниффинга формата. Всегда tool-call → JSON.
    """
    closure_max_tokens = llm_ep.max_tokens
    closure_ctk = chat_template_kwargs or llm_ep.chat_template_kwargs or None

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
        # call-site детерминирован резолвером точки вызова (без сниффинга формата). Сам tool-call —
        # общий проектный ``ainvoke_required_tool`` (force-tool + call_site = function.name +
        # ``correlation_with_call_site`` из ctxvar-снапшота). Здесь — выбор фазы и нативная wire-конверсия.
        # Корреляция через kwarg-мост (предсозданные воркеры lightrag, см. embed_func): берём
        # впрыснутую на submission-границе корреляцию, fallback на ctxvar (прод-путь без e2e).
        injected_corr = kwargs.pop(LIGHTRAG_CORRELATION_KWARG, None)
        kwargs.pop("response_format", None)  # 1.5-хинт «нужен JSON» — не нужен (всегда tool-call → JSON)
        _log_unsupported_llm_args(stream=stream, enable_cot=enable_cot, extra=kwargs)

        call_site = resolve_call_site(
            has_history=bool(history_messages),
            has_system_prompt=bool(system_prompt),
        )
        phase = lightrag_tool_phase_for_call_site(call_site.value)
        log.info(
            "lightrag_llm_call",
            call_site=call_site.value,
            has_history=bool(history_messages),
            prompt=prompt[:80],
        )
        tool_spec = load_tool_spec(phase.tool_spec_path)
        call = build_site_call(
            settings,
            None,
            _build_chat_messages(prompt, system_prompt, history_messages),
            endpoint=llm_ep,
            max_tokens=max_tokens if max_tokens is not None else closure_max_tokens,
            chat_template_kwargs=closure_ctk,
        )
        correlation_snap = injected_corr or get_litellm_correlation_from_ctxvar()
        context = f"LightRAG phase {call_site.value}"

        async def _attempt() -> str:
            msg = await ainvoke_required_tool(
                settings=settings,
                call=call,
                tool_spec=tool_spec,
                correlation_snap=correlation_snap,
                context=context,
            )
            args_struct = parse_tool_call_for_phase(msg, phase)
            result = to_lightrag_return_value(struct_to_lightrag_wire(phase, args_struct))
            log.info(
                "lightrag_tool_call",
                phase=phase.call_site.value,
                tool_name=phase.tool_name.value,
            )
            return result

        def _on_retry(attempt_no: int, exc: BaseException) -> None:
            log.warning(
                "lightrag_tool_bridge_retry",
                attempt=attempt_no,
                call_site=call_site.value,
                error=str(exc),
            )

        return await ainvoke_with_bridge_retries(
            max_attempts=_MAX_LIGHTRAG_TOOL_BRIDGE_RETRIES + 1,
            attempt=_attempt,
            retry_errors=(
                LiteLlmToolResponseError,
                LightragToolBridgeError,
                jsonschema.ValidationError,
            ),
            on_retry=_on_retry,
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
        # КОРРЕЛЯЦИЯ через kwarg (_threlium_e2e_correlation), а НЕ ctxvar: эта функция исполняется
        # в предсозданном воркере lightrag (priority_limit_async_func_call), чей контекст заморожен в
        # момент создания пула (bootstrap) — ctxvar там «протух». Мост в _construction впрыскивает
        # корреляцию в kwargs на submission-границе (правильный контекст запроса/индексации). Fallback
        # на ctxvar — для прод-пути без e2e-корреляции.
        correlation = _kwargs.pop(LIGHTRAG_CORRELATION_KWARG, None) or get_litellm_correlation_from_ctxvar()
        log.info(
            "lightrag_embed_call",
            n_texts=len(texts),
            context=_kwargs.get("context"),
            call_site=(correlation or {}).get("X-Threlium-Call-Site"),
            thread_root=(correlation or {}).get("X-Threlium-Thread-Root"),
            first_text=(texts[0][:80] if texts else ""),
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
        # rerank ставит свой call-site общим хелпером ``correlation_with_call_site`` (тем же, что внутри
        # ``ainvoke_required_tool`` для tool-вызовов): thread-root из ctxvar + гранулярный lightrag_query_rerank.
        base_corr = _kwargs.pop(LIGHTRAG_CORRELATION_KWARG, None) or get_litellm_correlation_from_ctxvar()
        correlation = correlation_with_call_site(
            base_corr,
            LitellmCallSite.LIGHTRAG_QUERY_RERANK.value,
        )
        log.info(
            "lightrag_rerank_call",
            n_docs=len(documents),
            call_site=correlation.get("X-Threlium-Call-Site"),
            query=query[:80],
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
            {"index": r.index, "relevance_score": r.relevance_score}
            for r in (resp.results or [])
        ]

    return rerank_func
