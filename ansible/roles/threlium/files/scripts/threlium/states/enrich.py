#!/usr/bin/env python3
"""enrich@localhost: notmuch-контекст + Jinja/LLM + LightRAG query_api → reasoning@localhost.

``docs/INDEX.md`` §7, ``docs/FSM.md`` §5.2, ADR 0001:

  * canonical входа: ``render_prompt(PromptPath.LIGHTRAG_ENRICH_INCOMING_USER_TEXT, incoming=msg)``;
  * план: ``render_prompt(PromptPath.LIGHTRAG_ENRICH_QUERY_PLAN)`` → один LLM →
    ``render_prompt(PromptPath.LIGHTRAG_ENRICH_AQUERY_USER)`` →
    ``run_rag_coroutine(rag.<query_api>(...), ...)`` (метод из ``settings.lightrag.query_api``);
  * envelope-dict собирается в Python, JSON — через ``json.dumps`` для ``<graph-answer>``;
  * ``build_enriched_multipart`` — ``multipart/mixed`` с гранулярными MIME-частями по ``Content-ID``.
"""
from __future__ import annotations

import asyncio
import copy
import json
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from email.message import EmailMessage
from typing import Any

from litellm.types.utils import Message
from lightrag import QueryParam

from threlium import nm as nmlib
from threlium.context_budget import (
    BucketConfig,
    BucketConfigTier,
    ContextMessageType,
    assign_tiers,
    classify_message_type,
    estimate_unified_weight,
    normalize_weights,
    score_messages,
    solve_mckp,
)
from threlium.settings import ThreliumSettings, resolve_llm_endpoint
from threlium.litellm_client import litellm_acompletion
from threlium.litellm_route_context import e2e_route_wire_tail, get_litellm_http_correlation
from threlium.enrich_context import build_unified_email_messages, trim_context_text, UnifiedEmailContext
from threlium.fsm_emit_semantic import emit_transition_simple_step_preserving_payload
from threlium.irt_chain import iter_in_reply_to_ancestors_from_inner_id
from threlium.logutil import logger
from threlium.litellm_wire import require_chat_model_response
from threlium.mime_reform import (
    EnrichPartId,
    build_enriched_multipart,
    email_message_from_path,
    extract_part_by_content_id,
)
from threlium.prompts import render_prompt
from threlium.response.collect import collect_ops
from threlium.response.state_summary import build_state_summary
from threlium.runners.lightrag import daemon_lightrag, run_rag_coroutine
from threlium.types import (
    EnrichGlobalMemoryText,
    EnrichGraphAnswerText,
    EnrichThreadMemoryText,
    EnrichUnifiedMailContextText,
    LightragPromptLibraryKey,
    LitellmCallSite,
    LiteLlmAcompletionKwargs,
    LiteLlmChatMessage,
    lite_llm_acompletion_to_dict,
    FsmStage,
    LightragLiteLlmCompletionBody,
    LitellmRoutingSite,
    MailHeaderName,
    NotmuchMessageIdInner,
    PromptPath,
    RfcMessageIdWire,
)
from threlium.types.litellm_correlation_header import LitellmCorrelationHeader

log = logger.bind(stage="enrich")

_HDR = MailHeaderName

_ALLOWED_QUERY_MODES = frozenset(
    {"local", "global", "hybrid", "naive", "mix", "bypass"}
)


def _query_param(cfg: ThreliumSettings) -> QueryParam:
    raw = (cfg.lightrag.query_mode or "hybrid").strip().lower()
    mode = raw if raw in _ALLOWED_QUERY_MODES else "hybrid"
    base = QueryParam()
    return replace(
        base,
        mode=mode,  # type: ignore[arg-type]
        top_k=cfg.lightrag.query_top_k,
        chunk_top_k=cfg.lightrag.query_chunk_top_k,
        max_total_tokens=cfg.lightrag.query_max_total_tokens,
        max_entity_tokens=cfg.lightrag.query_max_entity_tokens,
        max_relation_tokens=cfg.lightrag.query_max_relation_tokens,
        response_type=cfg.lightrag.query_response_type,
        enable_rerank=cfg.lightrag.enable_rerank,
    )


async def _enrich_llm_plan(cfg: ThreliumSettings, user_prompt: str) -> str:
    """Один LLM-вызов для формулировки запроса к LightRAG (маршрутизация LiteLLM)."""
    ep = resolve_llm_endpoint(cfg.litellm, LitellmRoutingSite.ENRICH_PLAN)
    mr = ep.max_retries if ep.max_retries is not None else cfg.litellm.max_retries
    log.info("litellm_routing", site=LitellmRoutingSite.ENRICH_PLAN.value, score=ep.score)
    call = LiteLlmAcompletionKwargs(
        model=ep.model,
        messages=[LiteLlmChatMessage(role="user", content=user_prompt)],
        timeout=float(ep.timeout),
        max_retries=mr,
        api_key=ep.api_key,
        api_base=ep.api_base,
        max_tokens=ep.max_tokens,
        chat_template_kwargs=ep.chat_template_kwargs or None,
    )
    call_kwargs = lite_llm_acompletion_to_dict(call)
    resp = require_chat_model_response(
        await litellm_acompletion(settings=cfg, **call_kwargs, stream=False)
    )
    choice = resp.choices[0]
    msg_obj: Message | None = choice.message
    content = ""
    if msg_obj is not None:
        raw_c = msg_obj.content
        content = LightragLiteLlmCompletionBody.parse(raw_c).value if isinstance(raw_c, str) else ""
    return str(content)


def _build_lightrag_envelope(
    *,
    raw_result: dict[str, Any] | str | None,
    query_api: str,
    query_mode: str,
    formulated_query: str,
) -> dict[str, Any]:
    """Envelope dict for Jinja ``| tojson(indent=2)``; no ``json.dumps`` in Python."""
    envelope: dict[str, Any] = {
        "query_api": query_api,
        "query_mode": query_mode,
        "ok": True,
        "threlium": {"formulated_query": formulated_query},
        "lightrag": {"raw": None, "llm_text": None},
    }
    if raw_result is None:
        envelope["lightrag"]["llm_text"] = None
        return envelope

    if query_api == "aquery":
        # aquery → str
        envelope["lightrag"]["llm_text"] = raw_result if isinstance(raw_result, str) else str(raw_result)
        return envelope

    if not isinstance(raw_result, dict):
        envelope["ok"] = False
        envelope["error"] = f"expected dict from {query_api}, got {type(raw_result).__name__}"
        return envelope

    if query_api == "aquery_data":
        envelope["lightrag"]["raw"] = raw_result
        return envelope

    # aquery_llm: sanitize response_iterator (not JSON-serializable)
    sanitized = copy.copy(raw_result)
    llm_resp = sanitized.get("llm_response")
    if isinstance(llm_resp, dict):
        llm_resp = dict(llm_resp)
        if llm_resp.get("is_streaming"):
            log.warning("aquery_llm_streaming_ignored")
        llm_resp.pop("response_iterator", None)
        sanitized["llm_response"] = llm_resp
        envelope["lightrag"]["llm_text"] = llm_resp.get("content")
    envelope["lightrag"]["raw"] = sanitized
    return envelope


@dataclass(frozen=True)
class EnrichResult:
    """Гранулярные компоненты enriched-контекста (заменяет монолитный payload)."""

    graph_answer: EnrichGraphAnswerText | None
    unified_mail_context: EnrichUnifiedMailContextText | None
    thread_memory: EnrichThreadMemoryText | None
    global_memory: EnrichGlobalMemoryText | None


_CARRY_OVER_IDS = (EnrichPartId.PLAN_STATE,)


def _collect_extra_parts(
    inner: NotmuchMessageIdInner, limit: int
) -> list[tuple[EnrichPartId, str]]:
    """Пересчёт ``<response-state>`` из CRDT + carry-over ``<plan-state>`` из e_prev."""
    parts: list[tuple[EnrichPartId, str]] = []

    ops = collect_ops(inner)
    summary = build_state_summary(ops)
    trimmed = trim_context_text(summary, limit)
    if trimmed:
        parts.append((EnrichPartId.RESPONSE_STATE, trimmed))

    chain = iter_in_reply_to_ancestors_from_inner_id(inner)
    for snap in chain:
        if snap.is_addressed_to_fsm_stage(FsmStage.REASONING):
            e_prev = email_message_from_path(snap.path)
            for pid in _CARRY_OVER_IDS:
                text = extract_part_by_content_id(e_prev, pid)
                if text is not None and text.strip():
                    parts.append((pid, trim_context_text(text.strip(), limit)))
            break

    return parts


def _render_mail_context(
    messages: list[EmailMessage],
    limit: int,
    *,
    tier_assignments: dict[int, int] | None = None,
    tier_assignments_types: dict[int, str] | None = None,
    preview_chars: int,
    total_messages: int,
) -> str:
    raw = render_prompt(
        PromptPath.LIGHTRAG_MAIL_CONTEXT,
        messages=messages,
        tier_assignments=tier_assignments or {},
        tier_assignments_types=tier_assignments_types or {},
        preview_chars=preview_chars,
        total_messages=total_messages,
    ).strip()
    return trim_context_text(raw, limit)


def _is_empty_rag_result(raw_result: dict[str, Any] | str | None, api: str) -> bool:
    """Check if RAG returned no useful context."""
    if raw_result is None:
        return True
    if isinstance(raw_result, str):
        stripped = raw_result.strip()
        return not stripped or stripped == "(no graph context)"
    if isinstance(raw_result, dict):
        if api == "aquery_llm":
            llm_resp = raw_result.get("llm_response")
            if isinstance(llm_resp, dict):
                content = llm_resp.get("content", "")
                if not content or content.strip() == "(no graph context)":
                    return True
        if api == "aquery_data":
            data = raw_result.get("data", {})
            if not data or (not data.get("entities") and not data.get("chunks")):
                return True
    return False


def _estimate_msgs_weight(msgs: list[EmailMessage]) -> int:
    """Оценка веса списка сообщений в full-body режиме (без Jinja)."""
    total = 0
    for m in msgs:
        msg_type = classify_message_type(m)
        if msg_type == ContextMessageType.SERVICE:
            total += 50
        else:
            part = m.get_body(preferencelist=("plain", "html"))
            body_chars = len(part.get_content()) if part else 0
            if msg_type == ContextMessageType.TOOL_OBSERVATION:
                total += min(body_chars, 500) + 100
            else:
                total += body_chars + 100
    return total


def _truncate_at_paragraph(text: str) -> str:
    """Обрезка до последней границы абзаца перед серединой текста."""
    if not text:
        return ""
    mid = len(text) // 2
    boundary = text.rfind("\n\n", 0, mid)
    if boundary > 0:
        return text[:boundary]
    return text[:mid]


async def _enrich_async(
    *,
    cfg: ThreliumSettings,
    question: str,
    scope: str,
    ctx: UnifiedEmailContext,
    rag_correlation: dict[str, str] | None,
    mckp_capacity: int,
    mckp_priorities: dict[EnrichPartId, float],
) -> EnrichResult:
    _plan_recent_n = cfg.enrich.plan_recent_n
    _recent_msgs = ctx.all_messages[-_plan_recent_n:] if ctx.all_messages else []
    _older_msgs = ctx.all_messages[:-_plan_recent_n] if len(ctx.all_messages) > _plan_recent_n else []
    _subject_skeleton = [
        {
            "date": m.get("Date", ""),
            "from": m.get("From", ""),
            "subject": m.get("Subject", ""),
        }
        for m in _older_msgs
    ]
    plan_prompt = render_prompt(
        PromptPath.LIGHTRAG_ENRICH_QUERY_PLAN,
        incoming_user_message=question,
        scope=scope,
        recent_messages=_recent_msgs,
        subject_skeleton=_subject_skeleton,
    )
    plan_prompt = trim_context_text(plan_prompt, mckp_capacity)
    formulated = (await _enrich_llm_plan(cfg, plan_prompt)).strip()
    if not formulated:
        formulated = question

    extra_instructions = cfg.lightrag.aquery_hints
    aquery_question = render_prompt(
        PromptPath.LIGHTRAG_ENRICH_AQUERY_USER,
        formulated_query=formulated,
        extra_instructions=extra_instructions,
    ).strip()
    if not aquery_question:
        raise RuntimeError("enrich: empty aquery question after template render")

    system_prompt = render_prompt(
        LightragPromptLibraryKey.RAG_RESPONSE.prompt_path(), scope=scope
    )
    rag = daemon_lightrag()
    if rag is None:
        raise RuntimeError("enrich: LightRAG daemon is not running (start_rag_loop_thread)")

    qparam = _query_param(cfg)
    api = cfg.lightrag.query_api

    async def _rag_query(q: str) -> dict[str, Any] | str | None:
        if api == "aquery":
            raw = await rag.aquery(
                q,
                param=qparam,
                system_prompt=system_prompt,
            )
            if isinstance(raw, AsyncIterator):
                raise RuntimeError("enrich: streaming LightRAG aquery is not supported")
            if raw is None:
                return None
            if not isinstance(raw, str):
                raise RuntimeError(
                    f"enrich: unexpected aquery return type {type(raw).__name__!r}"
                )
            return raw.strip() or None
        elif api == "aquery_data":
            return await rag.aquery_data(
                q,
                param=qparam,
            )
        elif api == "aquery_llm":
            return await rag.aquery_llm(
                q,
                param=qparam,
                system_prompt=system_prompt,
            )
        else:
            raise RuntimeError(f"enrich: unknown query_api {api!r}")

    raw_result = run_rag_coroutine(
        _rag_query(aquery_question), settings=cfg, correlation=rag_correlation
    )

    retried = False
    if _is_empty_rag_result(raw_result, api):
        log.info("enrich_rag_retry", reason="empty_first_attempt", formulated=formulated)
        retry_query = question if formulated != question else f"key facts about: {question}"
        raw_result = run_rag_coroutine(
            _rag_query(retry_query), settings=cfg, correlation=rag_correlation
        )
        retried = True
        if _is_empty_rag_result(raw_result, api):
            log.info("enrich_rag_retry_failed", formulated=retry_query)

    lightrag_envelope = _build_lightrag_envelope(
        raw_result=raw_result, query_api=api, query_mode=qparam.mode, formulated_query=formulated,
    )
    if retried:
        lightrag_envelope["retried"] = True

    log.info(
        "lightrag_envelope_meta",
        query_api=api,
        query_mode=qparam.mode,
        formulated_query=formulated,
        ok=lightrag_envelope.get("ok"),
    )

    llm_text = lightrag_envelope.get("lightrag", {}).get("llm_text")
    if llm_text and isinstance(llm_text, str) and llm_text.strip():
        graph_answer_raw = llm_text.strip()
    else:
        graph_answer_raw = json.dumps(
            lightrag_envelope.get("lightrag", {}).get("raw"),
            indent=2, ensure_ascii=False,
        ) if lightrag_envelope.get("lightrag", {}).get("raw") else ""

    _preview = cfg.enrich.tier_preview_chars
    _tier1 = cfg.enrich.tier1_full
    _tier2 = cfg.enrich.tier2_summary

    scored = score_messages(ctx.all_messages, cfg.enrich.message_type_weights()) if ctx.all_messages else ()

    # --- Phase 1: estimate weights for MCKP (no Jinja rendering) ---

    _tier1_med = max(1, _tier1 // 2)
    _tier2_med = max(1, _tier2 // 2)

    unified_configs = [
        BucketConfig(bucket=EnrichPartId.UNIFIED_MAIL_CONTEXT, tier=BucketConfigTier.FULL,
                     weight=estimate_unified_weight(scored, _tier1, _tier2, _preview) if scored else 0,
                     value=1.0, tier1_count=_tier1, tier2_count=_tier2),
        BucketConfig(bucket=EnrichPartId.UNIFIED_MAIL_CONTEXT, tier=BucketConfigTier.MEDIUM,
                     weight=estimate_unified_weight(scored, _tier1_med, _tier2_med, _preview) if scored else 0,
                     value=0.6, tier1_count=_tier1_med, tier2_count=_tier2_med),
        BucketConfig(bucket=EnrichPartId.UNIFIED_MAIL_CONTEXT, tier=BucketConfigTier.COMPACT,
                     weight=estimate_unified_weight(scored, _tier1, 0, _preview) if scored else 0,
                     value=0.3, tier1_count=_tier1, tier2_count=0),
        BucketConfig(bucket=EnrichPartId.UNIFIED_MAIL_CONTEXT, tier=BucketConfigTier.EMPTY,
                     weight=0, value=0.0, tier1_count=0, tier2_count=0),
    ]

    graph_signal = 1.0 if graph_answer_raw.strip() else 0.0
    thread_signal = 1.0 if ctx.thread_memory_msgs else 0.0
    global_signal = 1.0 if ctx.global_memory_msgs else 0.0

    graph_full_weight = len(graph_answer_raw)
    graph_medium_weight = graph_full_weight // 2

    thread_medium_msgs = ctx.thread_memory_msgs[len(ctx.thread_memory_msgs) // 2:]
    global_medium_msgs = ctx.global_memory_msgs[len(ctx.global_memory_msgs) // 2:]

    def _make_bucket_configs(
        bucket: EnrichPartId, full_weight: int, medium_weight: int, signal: float,
    ) -> list[BucketConfig]:
        configs = [
            BucketConfig(bucket=bucket, tier=BucketConfigTier.FULL,
                         weight=full_weight, value=signal,
                         tier1_count=0, tier2_count=0),
        ]
        if medium_weight > 0 and medium_weight < full_weight:
            configs.append(BucketConfig(bucket=bucket, tier=BucketConfigTier.MEDIUM,
                                        weight=medium_weight, value=0.5 * signal,
                                        tier1_count=0, tier2_count=0))
        configs.append(BucketConfig(bucket=bucket, tier=BucketConfigTier.EMPTY,
                                    weight=0, value=0.0, tier1_count=0, tier2_count=0))
        return configs

    bucket_configs_map: dict[EnrichPartId, list[BucketConfig]] = {
        EnrichPartId.GRAPH_ANSWER: _make_bucket_configs(
            EnrichPartId.GRAPH_ANSWER, graph_full_weight, graph_medium_weight, graph_signal),
        EnrichPartId.UNIFIED_MAIL_CONTEXT: unified_configs,
        EnrichPartId.THREAD_MEMORY: _make_bucket_configs(
            EnrichPartId.THREAD_MEMORY,
            _estimate_msgs_weight(ctx.thread_memory_msgs),
            _estimate_msgs_weight(thread_medium_msgs),
            thread_signal),
        EnrichPartId.GLOBAL_MEMORY: _make_bucket_configs(
            EnrichPartId.GLOBAL_MEMORY,
            _estimate_msgs_weight(ctx.global_memory_msgs),
            _estimate_msgs_weight(global_medium_msgs),
            global_signal),
    }

    # --- Phase 2: MCKP solve ---

    allocation = solve_mckp(bucket_configs_map, mckp_capacity, mckp_priorities)

    # --- Phase 3: lazy rendering — render only the chosen variant ---

    chosen_unified = allocation.get(EnrichPartId.UNIFIED_MAIL_CONTEXT)
    if chosen_unified and chosen_unified.tier != BucketConfigTier.EMPTY and ctx.all_messages:
        tiered = assign_tiers(scored, chosen_unified.tier1_count, chosen_unified.tier2_count)
        ta = {t.chronological_index: t.assigned_tier for t in tiered}
        ta_types = {t.chronological_index: t.msg_type.value for t in tiered}
        final_unified = _render_mail_context(
            ctx.all_messages, mckp_capacity,
            tier_assignments=ta, tier_assignments_types=ta_types,
            preview_chars=_preview, total_messages=len(ctx.all_messages),
        )
    else:
        final_unified = ""

    chosen_graph = allocation.get(EnrichPartId.GRAPH_ANSWER)
    if chosen_graph and chosen_graph.tier == BucketConfigTier.FULL:
        final_graph = graph_answer_raw
    elif chosen_graph and chosen_graph.tier == BucketConfigTier.MEDIUM:
        final_graph = _truncate_at_paragraph(graph_answer_raw)
    else:
        final_graph = ""

    chosen_thread = allocation.get(EnrichPartId.THREAD_MEMORY)
    if chosen_thread and chosen_thread.tier == BucketConfigTier.FULL:
        final_thread = _render_mail_context(
            ctx.thread_memory_msgs, mckp_capacity,
            tier_assignments=None, tier_assignments_types=None,
            preview_chars=_preview, total_messages=len(ctx.thread_memory_msgs),
        )
    elif chosen_thread and chosen_thread.tier == BucketConfigTier.MEDIUM:
        final_thread = _render_mail_context(
            thread_medium_msgs, mckp_capacity,
            tier_assignments=None, tier_assignments_types=None,
            preview_chars=_preview, total_messages=len(ctx.thread_memory_msgs),
        )
    else:
        final_thread = ""

    chosen_global = allocation.get(EnrichPartId.GLOBAL_MEMORY)
    if chosen_global and chosen_global.tier == BucketConfigTier.FULL:
        final_global = _render_mail_context(
            ctx.global_memory_msgs, mckp_capacity,
            tier_assignments=None, tier_assignments_types=None,
            preview_chars=_preview, total_messages=len(ctx.global_memory_msgs),
        )
    elif chosen_global and chosen_global.tier == BucketConfigTier.MEDIUM:
        final_global = _render_mail_context(
            global_medium_msgs, mckp_capacity,
            tier_assignments=None, tier_assignments_types=None,
            preview_chars=_preview, total_messages=len(ctx.global_memory_msgs),
        )
    else:
        final_global = ""

    graph_answer_vo = EnrichGraphAnswerText.parse(final_graph)

    log.info(
        "budget_allocation",
        capacity=mckp_capacity,
        graph_tier=chosen_graph.tier if chosen_graph else "none",
        unified_tier=chosen_unified.tier if chosen_unified else "none",
        thread_tier=chosen_thread.tier if chosen_thread else "none",
        global_tier=chosen_global.tier if chosen_global else "none",
        total_weight=sum(c.weight for c in allocation.values()),
    )

    return EnrichResult(
        graph_answer=graph_answer_vo if graph_answer_vo.value else None,
        unified_mail_context=EnrichUnifiedMailContextText.parse(final_unified) if final_unified else None,
        thread_memory=EnrichThreadMemoryText.parse(final_thread) if final_thread else None,
        global_memory=EnrichGlobalMemoryText.parse(final_global) if final_global else None,
    )


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    mid_w = RfcMessageIdWire.parse_present_from_email(msg, _HDR.MESSAGE_ID)
    inner = NotmuchMessageIdInner.from_optional_wire(mid_w)
    tid_vo = nmlib.thread_id_for_optional_message_id(inner)
    if tid_vo is None:
        raise RuntimeError("enrich: notmuch has no thread_id for this message (is it indexed yet?)")

    ctx = build_unified_email_messages(
        settings=config,
        leaf_inner=inner,
        thread_id=tid_vo.value,
    )

    limit = config.enrich.context_max_chars
    _all_priorities = {
        EnrichPartId.USER_MESSAGE: config.enrich.priority_user,
        EnrichPartId.GRAPH_ANSWER: config.enrich.priority_graph,
        EnrichPartId.UNIFIED_MAIL_CONTEXT: config.enrich.priority_unified,
        EnrichPartId.THREAD_MEMORY: config.enrich.priority_thread_mem,
        EnrichPartId.GLOBAL_MEMORY: config.enrich.priority_global_mem,
        EnrichPartId.RESPONSE_STATE: config.enrich.priority_extra,
    }
    _norm = normalize_weights(_all_priorities)
    budget_user = int(limit * _norm[EnrichPartId.USER_MESSAGE])
    budget_extra = int(limit * _norm[EnrichPartId.RESPONSE_STATE])
    user_message_text = trim_context_text(
        render_prompt(
            PromptPath.LIGHTRAG_ENRICH_INCOMING_USER_TEXT,
            incoming=msg,
        ).strip(),
        budget_user,
    )
    if not user_message_text:
        raise RuntimeError(
            f"enrich: empty user message after incoming template ({_HDR.SUBJECT} / body)"
        )

    scope = tid_vo.as_notmuch_thread_term()
    rag_correlation: dict[str, str] | None = None
    if config.e2e.litellm_route_correlation:
        snap = get_litellm_http_correlation()
        th = threading.current_thread()
        route_k = _HDR.ROUTE.value
        route_v = snap.get(route_k) if snap else None
        rt = route_v if isinstance(route_v, str) else None
        log.debug(
            "e2e_litellm_tls",
            thread_name=th.name,
            thread_ident=threading.get_ident(),
            snap_is_none=snap is None,
            snap_keys=sorted(snap.keys()) if snap else [],
            route_header_present=bool(snap and route_k in snap),
            route_tail=e2e_route_wire_tail(rt),
            message_id=mid_w.value if mid_w else None,
        )
        rag_correlation = dict(snap) if snap else None
        if rag_correlation is not None:
            rag_correlation[LitellmCorrelationHeader.CALL_SITE.value] = (
                LitellmCallSite.LIGHTRAG_QUERY.value
            )
        log.debug(
            "asyncio_run_starting",
            fsm_thread=th.name,
            ident=threading.get_ident(),
            message_id=mid_w.value if mid_w else None,
        )
    mckp_capacity = max(0, limit - budget_user - budget_extra)
    mckp_priorities = {
        EnrichPartId.GRAPH_ANSWER: config.enrich.priority_graph,
        EnrichPartId.UNIFIED_MAIL_CONTEXT: config.enrich.priority_unified,
        EnrichPartId.THREAD_MEMORY: config.enrich.priority_thread_mem,
        EnrichPartId.GLOBAL_MEMORY: config.enrich.priority_global_mem,
    }
    result = asyncio.run(
        _enrich_async(
            cfg=config,
            question=user_message_text,
            scope=scope,
            ctx=ctx,
            rag_correlation=rag_correlation,
            mckp_capacity=mckp_capacity,
            mckp_priorities=mckp_priorities,
        )
    )

    extra_parts = _collect_extra_parts(inner, budget_extra) if inner is not None else []

    enriched = build_enriched_multipart(
        msg,
        user_message_text=trim_context_text(user_message_text, budget_user),
        graph_answer=result.graph_answer,
        unified_mail_context=result.unified_mail_context,
        thread_memory=result.thread_memory,
        global_memory=result.global_memory,
        stage=stage.value,
        extra_parts=extra_parts or None,
    )
    return emit_transition_simple_step_preserving_payload(
        enriched, to_addr=FsmStage.REASONING, from_stage=stage, settings=config,
    )
