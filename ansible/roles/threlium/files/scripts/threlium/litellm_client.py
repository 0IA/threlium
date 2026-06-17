"""Единые вызовы LiteLLM с ``extra_headers`` e2e-корреляции.

Источник корреляции (приоритет):
1. Явный ``correlation_override`` (для tool-вызовов, см. ``correlation_with_call_site``).
2. ``ContextVar`` (:func:`get_litellm_correlation_from_ctxvar`) — единый носитель и для asyncio
   RAG-задач (наследуется через ``create_task``), и для синхронных FSM-стадий (per-thread set→read,
   скоуп на сообщение через token-reset во ``fsm._run_stage``).

Снаружи процесса вызовов LiteLLM — :func:`merge_litellm_call_kwargs_and_log`: при
``ThreliumSettings.e2e.litellm_route_correlation`` подмешивает заголовки и пишет одну строку отладки
(сводка + ``GET`` WireMock ``/state-extension/contexts``); без флага возвращает ``dict(kwargs)``
без merge и без логов.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from threlium import openai_compatible_client

from threlium.settings import ThreliumSettings, resolve_llm_endpoint
from threlium.logutil import logger
from threlium.types.litellm_routing_site import LitellmRoutingSite
from threlium.litellm_route_context import (
    e2e_route_wire_tail,
    get_litellm_correlation_from_ctxvar,
)
from threlium.types import MailHeaderName
from threlium.types.litellm_call_site import LitellmCallSite

_REASONING_CALL_SITE = LitellmCallSite.REASONING.value
from threlium.types.litellm_correlation_header import LitellmCorrelationHeader

_HDR_VALUE_MAX = 160

log = logger.bind(stage="litellm")

# Внутренние ключи в dict корреляции (ctxvar); не отправляются в HTTP ``extra_headers``
# (фильтр — :func:`_is_internal_correlation_key` по этому префиксу).
_INTERNAL_PREFIX = "__threlium_litellm_"
# Сквозной номер вызова LLM в треде = длина IRT-цепочки на момент стадии (``thread_len``).
# Выставляется ``runners/engine/fsm.py`` из кэш-материализованной цепочки (без notmuch-round-trip).
LITELLM_THREAD_LEN_SLOT = f"{_INTERNAL_PREFIX}thread_len"


def _parse_seq_cell(raw: object) -> int:
    if raw is None:
        return 0
    try:
        return max(0, int(str(raw).strip()))
    except ValueError as exc:
        log.warning("seq_cell_parse_failed", raw=repr(raw), exc_info=exc)
        return 0


def _assign_litellm_request_seq(correlation: dict[str, str]) -> int:
    """Сквозной номер вызова LLM в треде = ``thread_len`` (длина IRT-цепочки на момент стадии), из
    internal-слота корреляции (выставлен в ``fsm._run_stage`` из кэш-материализованной цепочки). Идёт в
    wire-заголовок ``X-Threlium-Litellm-Req-Seq``.

    На стадию приходится ровно ОДИН stub-значимый completion (reasoning ``_decide`` без in-place-петли —
    ошибки уходят в ``enrich_fast`` новым письмом → новый ``thread_len``), поэтому пара
    ``(X-Threlium-Call-Site, thread_len)`` однозначно адресует вызов; внутристадийный ``req_delta`` не
    нужен. Вне FSM-стадии (indexer) слот отсутствует → ``0`` (indexer-стабы по seq не матчатся).
    Идемпотентно к ретраю: та же стадия → тот же ``thread_len`` (litellm повторяет HTTP теми же
    ``extra_headers``; worker-restart переигрывает стадию с тем же ``thread_len``)."""

    return _parse_seq_cell(correlation.get(LITELLM_THREAD_LEN_SLOT))


def _is_internal_correlation_key(key: str) -> bool:
    return key.startswith(_INTERNAL_PREFIX)


def _clip_header_value(value: object) -> str:
    s = str(value)
    if len(s) <= _HDR_VALUE_MAX:
        return s
    return f"{s[: _HDR_VALUE_MAX - 3]}..."


def _extra_headers_summary(merged: dict[str, Any]) -> str:
    raw = merged.get("extra_headers")
    if not isinstance(raw, dict) or not raw:
        return "extra_headers=(none)"
    parts = [f"{k}={_clip_header_value(v)!r}" for k, v in sorted(raw.items(), key=lambda kv: kv[0])]
    return "extra_headers[" + " ".join(parts) + "]"


def _e2e_wiremock_state_extension_contexts_url(
    merged: dict[str, Any], *, settings: ThreliumSettings
) -> str | None:
    """``http(s)://host:port/__admin/state-extension/contexts`` из ``api_base`` запроса или каталога ``litellm_routing``."""

    raw = merged.get("api_base")
    base = str(raw).strip() if raw is not None else ""
    if not base:
        fb = resolve_llm_endpoint(settings.litellm, LitellmRoutingSite.REASONING).api_base
        base = (fb or "").strip() if fb else ""
    if not base:
        return None
    parsed = urllib.parse.urlparse(base)
    if not parsed.scheme or not parsed.netloc:
        return None
    root = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return f"{root}/__admin/state-extension/contexts"


def _e2e_route_wire_from_extra_headers(extra: dict[str, Any]) -> str | None:
    want = MailHeaderName.ROUTE.value.casefold()
    for k, v in extra.items():
        if str(k).casefold() == want:
            s = str(v).strip()
            return s if s else None
    return None


def _log_e2e_litellm_correlation_outbound_and_wiremock_contexts(
    settings: ThreliumSettings,
    merged: dict[str, Any],
    *,
    kind: str,
    stream: bool | None,
) -> None:
    """Сводка исходящего вызова + список контекстов WireMock State Extension (без проверки флага)."""

    kw: dict[str, Any] = {
        "kind": kind,
        "model": merged.get("model"),
        "api_base": merged.get("api_base"),
        "extra_headers": _extra_headers_summary(merged),
    }
    if stream is not None:
        kw["stream"] = stream
    if kind in ("acompletion", "completion_sync"):
        msgs = merged.get("messages")
        if isinstance(msgs, list):
            kw["messages_n"] = len(msgs)
    elif kind == "aembedding":
        inp = merged.get("input")
        if isinstance(inp, list):
            kw["embedding_input_n"] = len(inp)
        elif inp is not None:
            kw["embedding_input_n"] = 1
    elif kind == "arerank":
        docs = merged.get("documents")
        if isinstance(docs, list):
            kw["rerank_docs_n"] = len(docs)

    extra_raw = merged.get("extra_headers")
    extra_dict: dict[str, Any] = extra_raw if isinstance(extra_raw, dict) else {}
    route_val = _e2e_route_wire_from_extra_headers(extra_dict)

    url = _e2e_wiremock_state_extension_contexts_url(merged, settings=settings)
    if not url:
        log.debug("e2e_litellm_outbound", wm_contexts="skip GET: no api_base",
                  route_tail=e2e_route_wire_tail(route_val), **kw)
        return

    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        ctx_list: list[str] = data if isinstance(data, list) else []
        tails = [e2e_route_wire_tail(str(c), tail_n=28) for c in ctx_list[:5]]
        match = route_val in ctx_list if route_val else None
        kw.update(
            wm_contexts_url=url,
            wm_contexts_count=len(ctx_list),
            route_in_wm=match,
            route_tail=e2e_route_wire_tail(route_val),
            wm_context_tails=tails,
        )
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
        kw.update(
            wm_contexts_url=url,
            wm_contexts_get_failed=repr(e),
            route_tail=e2e_route_wire_tail(route_val),
        )

    log.debug("e2e_litellm_outbound", **kw)


def _merge_litellm_extra_route_headers(
    kwargs: dict[str, Any],
    *,
    correlation_override: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Корреляция → ``extra_headers`` + сквозной номер вызова ``LITELLM_REQUEST_SEQ`` (= ``thread_len``).

    Источник корреляции (приоритет): ``correlation_override`` > ContextVar.

    В wire один заголовок :attr:`MailHeaderName.LITELLM_REQUEST_SEQ` = ``thread_len`` (см.
    :func:`_assign_litellm_request_seq`). Internal-слоты (``__threlium_litellm_*``) в HTTP не попадают.

    Всегда работает с копией ``kwargs``. Существующие ``extra_headers`` сохраняются; ключи из
    источника перекрывают совпадающие (кроме внутренних слотов).

    Снаружи модуля не вызывать — только :func:`merge_litellm_call_kwargs_and_log`.
    """

    co = correlation_override
    cv = get_litellm_correlation_from_ctxvar()
    correlation = co or cv
    source = "override" if co else ("ctxvar" if cv else "NONE")
    thread_root = correlation.get("X-Threlium-Thread-Root") if correlation else None
    log.debug(
        "merge_correlation",
        source=source,
        thread_root=thread_root,
        override_present=co is not None,
        ctxvar_present=cv is not None,
    )
    out = dict(kwargs)
    raw_extra = out.get("extra_headers")
    extra: dict[str, str] = {}
    if isinstance(raw_extra, dict):
        for k, v in raw_extra.items():
            ks = str(k)
            if _is_internal_correlation_key(ks):
                continue
            extra[ks] = str(v)
    wire_seq_key = LitellmCorrelationHeader.LITELLM_REQUEST_SEQ.value
    if correlation:
        for k, v in correlation.items():
            ks = str(k)
            if _is_internal_correlation_key(ks):
                continue
            extra[ks] = str(v)
        seq_val = _assign_litellm_request_seq(correlation)
        extra[wire_seq_key] = str(seq_val)
    if not extra:
        return out
    out["extra_headers"] = extra
    return out


def _single_tool_function_name(tools: object) -> str | None:
    """``function.name`` ровно одного tool из payload (иначе ``None``)."""
    if not isinstance(tools, list) or len(tools) != 1:
        return None
    spec = tools[0]
    if not isinstance(spec, dict):
        return None
    func = spec.get("function")
    if not isinstance(func, dict):
        return None
    name = func.get("name")
    return name if isinstance(name, str) and name.strip() else None


def _assert_single_tool_call_site(merged: dict[str, Any]) -> None:
    """Инвариант e2e: при одном tool ``X-Threlium-Call-Site`` == ``function.name``.

    Исключения: reasoning umbrella (``call_site == reasoning``; единственный chat-вызов
    с переменным числом tools, в т.ч. одним response_finalize при budget-exhausted) и
    не-tool вызовы (embedding / rerank — у них нет ``tools``).
    """
    name = _single_tool_function_name(merged.get("tools"))
    if name is None:
        return
    extra = merged.get("extra_headers")
    if not isinstance(extra, dict):
        return
    cs_key = LitellmCorrelationHeader.CALL_SITE.value
    call_site = None
    for k, v in extra.items():
        if str(k).casefold() == cs_key.casefold():
            call_site = str(v)
            break
    if call_site is None or call_site == name or call_site == _REASONING_CALL_SITE:
        return
    raise RuntimeError(
        "litellm tool invariant: single-tool call_site mismatch — "
        f"X-Threlium-Call-Site={call_site!r} != function.name={name!r}"
    )


def merge_litellm_call_kwargs_and_log(
    *,
    settings: ThreliumSettings,
    kwargs: dict[str, Any],
    log_kind: str,
    stream: bool | None,
    correlation_override: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Единственная точка с проверкой ``e2e_litellm_route_correlation``: merge + отладка WireMock."""

    if not settings.e2e.litellm_route_correlation:
        return dict(kwargs)
    merged = _merge_litellm_extra_route_headers(
        dict(kwargs),
        correlation_override=correlation_override,
    )
    _assert_single_tool_call_site(merged)
    _log_e2e_litellm_correlation_outbound_and_wiremock_contexts(
        settings, merged, kind=log_kind, stream=stream
    )
    return merged


async def litellm_acompletion(
    *,
    settings: ThreliumSettings,
    stream: bool = False,
    correlation_override: dict[str, str] | None = None,
    **kwargs: Any,
) -> Any:
    merged = merge_litellm_call_kwargs_and_log(
        settings=settings,
        kwargs=dict(kwargs),
        log_kind="acompletion",
        stream=stream,
        correlation_override=correlation_override,
    )
    return await openai_compatible_client.chat_completions_async(merged)


async def litellm_aembedding(
    *,
    settings: ThreliumSettings,
    correlation_override: dict[str, str] | None = None,
    **kwargs: Any,
) -> Any:
    merged = merge_litellm_call_kwargs_and_log(
        settings=settings,
        kwargs=dict(kwargs),
        log_kind="aembedding",
        stream=None,
        correlation_override=correlation_override,
    )
    return await openai_compatible_client.embeddings_async(merged)


async def litellm_arerank(
    *,
    settings: ThreliumSettings,
    correlation_override: dict[str, str] | None = None,
    **kwargs: Any,
) -> Any:
    merged = merge_litellm_call_kwargs_and_log(
        settings=settings,
        kwargs=dict(kwargs),
        log_kind="arerank",
        stream=None,
        correlation_override=correlation_override,
    )
    return await openai_compatible_client.rerank_async(merged)


def litellm_completion_sync(
    *,
    settings: ThreliumSettings,
    stream: bool = False,
    correlation_override: dict[str, str] | None = None,
    **kwargs: Any,
) -> Any:
    merged = merge_litellm_call_kwargs_and_log(
        settings=settings,
        kwargs=dict(kwargs),
        log_kind="completion_sync",
        stream=stream,
        correlation_override=correlation_override,
    )
    return openai_compatible_client.chat_completions_sync(merged)


