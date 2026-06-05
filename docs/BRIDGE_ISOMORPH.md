# Канал `isomorph` — входящий HTTP-мост для нескольких LLM API

`isomorph` — первый **входящий** HTTP-сервер Threlium. Для агентских клиентов (Cline, Cursor,
Continue, любой OpenAI/Anthropic-совместимый клиент) он выглядит обычным LLM-провайдером, а внутри
прогоняет запрос через стандартный FSM-контур по схеме **long-hold + egress-push**.

Процесс: `threlium-bridge@isomorph` (systemd-инстанс, uvicorn). Реализация —
[`bridges/isomorph/`](../ansible/roles/threlium/files/scripts/threlium/bridges/isomorph/); FSM-стадия —
[`states/egress_isomorph.py`](../ansible/roles/threlium/files/scripts/threlium/states/egress_isomorph.py).

## Endpoints (MVP / Phase A)

| Method | Path | api_surface | FSM | Назначение |
|--------|------|-------------|-----|-----------|
| POST | `/v1/messages` | `anthropic_messages` | да | Anthropic Messages; SSE при `stream`, иначе JSON `Message` |
| POST | `/v1/chat/completions` | `openai_chat_completions` | да | OpenAI chat-completions; `stream:true`→SSE, иначе JSON |
| GET | `/v1/models` | — | нет | `{object:"list",data:[{id}]}` из `settings.litellm` (опц.) |
| GET | `/health` | — | нет | ops/readiness |
| POST | `/internal/v1/push` | — | нет | egress→мост; localhost + `push_secret`; идемпотентно |

Auth: один `bridges.isomorph.api_key` (Anthropic `x-api-key` ИЛИ OpenAI `Authorization: Bearer`).
Base URL клиента: `http://<host>:<listen_port>/v1` (SDK добавляет `/messages` / `/chat/completions`).

## Поток одного хода

```
Клиент → POST (полная история) → мост: tail-extraction + content-addressed MID (чистый compute)
       → register pending(request_id) → 200 + SSE keep-alive (если stream)
       → deliver(ingress email) → FSM (enrich → reasoning → … → egress_router → egress_isomorph)
       → egress: archive glue (FIRST) → POST /internal/v1/push → мост: SSE-чанки + [DONE] / JSON
```

reasoning синхронный и терминальный → **Phase A синтезирует полную SSE-цепочку из одного push**
(не живой стрим). Keep-alive держит соединение, пока FSM работает.

## Тред-непрерывность (контент-адресуемые Message-ID)

Клиент (и production VSCode-расширение, и CLI) шлёт **полную self-contained историю** в каждом
запросе (stateless-природа OpenAI/Anthropic API), без `In-Reply-To`. Мост восстанавливает стык
**без чтения notmuch**, потому что каждый isomorph-MID = `canon(IsomorphContentId(hash(контент)))`:

- `egress_isomorph` минтит glue `Message-ID = canon(hash(ответ Threlium))`;
- следующий запрос несёт этот ответ как **last-assistant** → мост пересчитывает тот же хеш и ставит
  его как `In-Reply-To` нового ingress → notmuch связывает тред сам (MID/IRT-threading);
- `Message-ID` нового ingress = `canon(hash(хвост, parent))` → идемпотентность ретраев.

Это **email-glue с хешем ответа вместо внешнего SMTP-MID** (Cline возвращает текст ответа, не MID).
Для FSM isomorph НЕОТЛИЧИМ от email/tg/mx; **FSM не меняется**. Нормализация хеша — общий модуль
[`types/isomorph_content.py`](../ansible/roles/threlium/files/scripts/threlium/types/isomorph_content.py):
text-блоки + сигнатура tool_use, исключая thinking/`cache_control`; tool-call `id` включается на
Anthropic, исключается на OpenAI (SDK усекает). Детали инвариантов — [THREAD_MODEL §6](THREAD_MODEL.md#6-канал-isomorph-контент-адресуемые-message-id-glue-без-внешнего-mid).

**ARCHIVE-FIRST**: egress пишет glue до push — иначе Cline пришлёт следующий запрос раньше записи
glue → orphan-форк. Первый ход (нет last-assistant) или редкий промах хеша → orphan → новый тред.
Повтор байт-идентичного ответа в треде → один MID → форк ветки (benign, FSM терпит).

## Два клиент-стека (verified против vendor/cline)

| Клиент | SDK | Роль |
|--------|-----|------|
| **VSCode-расширение** | официальные `openai@6.21.0` / `@anthropic-ai/sdk@0.37.0` | **production** (строгая планка wire) |
| **Cline CLI** | Vercel AI SDK (`@ai-sdk/openai`/`@ai-sdk/anthropic` v3) | тест-харнесс (e2e) |

Wire — стандарт-совместимый под обоих; официальные SDK строже. Особенности: `include_usage`
форсирован OpenAI-провайдером → usage-чанк обязателен (с пустым `choices:[]`); `[DONE]` терминатор;
стрим НЕ под жёстким 30 c (это `fetchJson` CLI для не-стрим JSON) — граница ~undici 300 c → keep-alive
~20–30 c; запрос Anthropic несёт `system`-массив + `cache_control` + `betas` — мост их игнорирует.

## Settings (`bridges.isomorph`)

`listen_host`, `listen_port` (bind + таргет push), `api_key`, `push_secret`, `request_timeout_sec`,
`keepalive_sec`, `graceful_shutdown_sec`, `enabled_surfaces`. Env: `THRELIUM_BRIDGES__ISOMORPH__*`.
Пустой `api_key` → мост не стартует (`bridge_readiness`).

## Bake (только e2e)

Cline CLI (Node.js) запекается в SUT-образ **только** e2e-harness'ом
([`tests/e2e/scripts/bake_e2e_sut_image.sh`](../tests/e2e/scripts/bake_e2e_sut_image.sh):
`docker exec` NodeSource Node 22 + `npm i -g cline` перед `docker commit`). **НЕ часть прод-деплоя**
(`site.yml` Node/Cline не ставит). `starlette`/`uvicorn`/`anyio` — обычные prod-зависимости моста.

## Вне scope MVP

Phase 1.5: `POST /v1/responses` (`openai_responses`), `GET /v1/model/info` (LiteLLM-провайдер Cline).
Phase B: живой стриминг (инкрементальный push из стрим-режима reasoning). Embeddings, legacy
`/v1/completions`, Codex, wss — Cline agent loop не использует.
