"""Тонкий OpenAI-совместимый HTTP-клиент (httpx) — замена litellm в нашем коде.

Покрывает то подмножество, что использует Threlium: ``/chat/completions`` (chat+tools,
не-stream), ``/embeddings``, vLLM ``/rerank``. Маршрутизация (model/api_base/api_key) —
уже наша (``resolve_llm_endpoint``); здесь только транспорт + ретраи + декод в VO.

Зависимости — ``httpx`` + ``tenacity`` + ``msgspec`` (~0.08s импорт против ~1.65s litellm).
Ретраи: ``tenacity`` на сетевых ошибках/таймаутах и HTTP 429/5xx, до ``max_retries`` доп.
попыток. Прочие 4xx/невалидный ответ → :class:`ThreliumLlmError` (ловится error-конвертом FSM).
"""
from __future__ import annotations

import asyncio
import ssl
from typing import Any

import httpx
import msgspec
from tenacity import (
    AsyncRetrying,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from threlium import llm_wire

_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
# Транспортные ключи payload (не уходят в JSON-тело запроса).
_TRANSPORT_KEYS = frozenset({"api_base", "api_key", "timeout", "max_retries", "extra_headers"})
_WAIT = wait_exponential(multiplier=0.3, min=0.3, max=8.0)

# --- общий SSL-контекст + пул клиентов + msgspec-энкодер ------------------------------------
# Построение SSL-контекста (загрузка CA-бандла) — ДОРОГО: при создании нового httpx-клиента
# на КАЖДЫЙ вызов это съедало ~1/3 CPU движка под -n2 (профиль py-spy: ssl.create_default_context).
# Строим контекст ОДИН раз и переиспользуем; клиенты пулим. msgspec-энкодер вместо stdlib json
# (httpx ``json=`` идёт через json.dumps — ещё ~15% CPU на сериализации тел запросов).
try:  # CA-бандл httpx по умолчанию берёт из certifi — повторяем, чтобы не менять прод-доверие.
    import certifi

    _SSL_CONTEXT: ssl.SSLContext = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover - на проде certifi есть (зависимость httpx)
    _SSL_CONTEXT = ssl.create_default_context()

_JSON_ENCODER = msgspec.json.Encoder()

# Пул sync-клиента: httpx.Client не привязан к event-loop и thread-safe для запросов → один на процесс.
_sync_client: httpx.Client | None = None
# Пул async-клиентов: httpx.AsyncClient привязан к своему event-loop, а движок крутит много
# короткоживущих циклов (asyncio.run на стадию FSM) + демон-loop LightRAG. Кэшируем по running-loop
# и переиспользуем внутри цикла; мёртвые (закрытый loop) подчищаем. id(loop) может переиспользоваться
# после GC — поэтому храним (loop, client) и сверяем тождество loop.
_async_clients: dict[int, tuple[asyncio.AbstractEventLoop, httpx.AsyncClient]] = {}


def _get_sync_client() -> httpx.Client:
    global _sync_client
    if _sync_client is None or _sync_client.is_closed:
        _sync_client = httpx.Client(verify=_SSL_CONTEXT)
    return _sync_client


def _get_async_client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    key = id(loop)
    entry = _async_clients.get(key)
    if entry is not None and entry[0] is loop and not entry[1].is_closed:
        return entry[1]
    for k, (lp, _cl) in list(_async_clients.items()):
        if lp.is_closed():
            _async_clients.pop(k, None)
    client = httpx.AsyncClient(verify=_SSL_CONTEXT)
    _async_clients[key] = (loop, client)
    return client


class ThreliumLlmError(RuntimeError):
    """Ошибка вызова LLM (не-ретраябельный HTTP / исчерпаны ретраи / битый ответ)."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class _RetryableHttp(Exception):
    """Внутренний сигнал ретраябельного HTTP-статуса для tenacity."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"retryable HTTP {status}")
        self.status = status
        self.body = body


_RETRY_EXC = (_RetryableHttp, httpx.TransportError, httpx.TimeoutException)


def _endpoint_url(api_base: str | None, path: str) -> str:
    base = (api_base or "").strip()
    if not base:
        raise ThreliumLlmError(f"LLM call has no api_base for {path}")
    return base.rstrip("/") + path


def _headers(api_key: str | None, extra_headers: dict[str, Any] | None) -> dict[str, str]:
    h: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        for k, v in extra_headers.items():
            h[str(k)] = str(v)
    return h


def _split_transport(payload: dict[str, Any]) -> tuple[dict[str, Any], str | None, str | None, float, int, dict[str, Any] | None]:
    """payload → (json_body, api_base, api_key, timeout, max_retries, extra_headers)."""
    api_base = payload.get("api_base")
    api_key = payload.get("api_key")
    timeout = float(payload.get("timeout") or 60.0)
    max_retries = int(payload.get("max_retries") or 0)
    extra_headers = payload.get("extra_headers")
    body = {k: v for k, v in payload.items() if k not in _TRANSPORT_KEYS}
    # response_format пропускаем только как JSON-совместимый dict (Pydantic-класс на wire не идёт).
    rf = body.get("response_format")
    if rf is not None and not isinstance(rf, (dict, str)):
        body.pop("response_format", None)
    return body, api_base, api_key, timeout, max_retries, extra_headers


def _raise_for_status(status: int, text: str) -> None:
    if status in _RETRYABLE_STATUS:
        raise _RetryableHttp(status, text)
    if status >= 400:
        raise ThreliumLlmError(f"LLM HTTP {status}", status=status, body=text)


def _post_sync(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float, max_retries: int) -> bytes:
    content = _JSON_ENCODER.encode(body)

    def _do() -> bytes:
        r = _get_sync_client().post(url, headers=headers, content=content, timeout=timeout)
        _raise_for_status(r.status_code, r.text)
        return r.content

    retryer = Retrying(
        stop=stop_after_attempt(max(1, max_retries + 1)),
        wait=_WAIT,
        retry=retry_if_exception_type(_RETRY_EXC),
        reraise=True,
    )
    try:
        return retryer(_do)
    except _RetryableHttp as e:
        raise ThreliumLlmError(f"LLM HTTP {e.status} after {max_retries} retries", status=e.status, body=e.body) from e
    except (httpx.TransportError, httpx.TimeoutException) as e:
        raise ThreliumLlmError(f"LLM transport error after {max_retries} retries: {e}") from e


async def _post_async(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float, max_retries: int) -> bytes:
    content = _JSON_ENCODER.encode(body)

    async def _do() -> bytes:
        r = await _get_async_client().post(url, headers=headers, content=content, timeout=timeout)
        _raise_for_status(r.status_code, r.text)
        return r.content

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max(1, max_retries + 1)),
            wait=_WAIT,
            retry=retry_if_exception_type(_RETRY_EXC),
            reraise=True,
        ):
            with attempt:
                return await _do()
    except _RetryableHttp as e:
        raise ThreliumLlmError(f"LLM HTTP {e.status} after {max_retries} retries", status=e.status, body=e.body) from e
    except (httpx.TransportError, httpx.TimeoutException) as e:
        raise ThreliumLlmError(f"LLM transport error after {max_retries} retries: {e}") from e
    raise ThreliumLlmError("LLM retry loop exhausted without result")  # unreachable


# ---- chat completions ----

def chat_completions_sync(payload: dict[str, Any]) -> llm_wire.LlmChatResponse:
    body, api_base, api_key, timeout, mr, extra = _split_transport(payload)
    raw = _post_sync(_endpoint_url(api_base, "/chat/completions"), _headers(api_key, extra), body, timeout, mr)
    return llm_wire.decode_chat_response(raw)


async def chat_completions_async(payload: dict[str, Any]) -> llm_wire.LlmChatResponse:
    body, api_base, api_key, timeout, mr, extra = _split_transport(payload)
    raw = await _post_async(_endpoint_url(api_base, "/chat/completions"), _headers(api_key, extra), body, timeout, mr)
    return llm_wire.decode_chat_response(raw)


# ---- embeddings ----

async def embeddings_async(payload: dict[str, Any]) -> llm_wire.LlmEmbeddingResponse:
    body, api_base, api_key, timeout, mr, extra = _split_transport(payload)
    raw = await _post_async(_endpoint_url(api_base, "/embeddings"), _headers(api_key, extra), body, timeout, mr)
    return llm_wire.decode_embedding_response(raw)


# ---- rerank (vLLM /rerank; OpenAI SDK has none) ----

async def rerank_async(payload: dict[str, Any]) -> llm_wire.LlmRerankResponse:
    body, api_base, api_key, timeout, mr, extra = _split_transport(payload)
    # vLLM rerank — не OpenAI-эндпоинт; custom_llm_provider не идёт на wire.
    body.pop("custom_llm_provider", None)
    raw = await _post_async(_endpoint_url(api_base, "/rerank"), _headers(api_key, extra), body, timeout, mr)
    return llm_wire.decode_rerank_response(raw)
