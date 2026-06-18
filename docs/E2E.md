# Threlium — E2E: harness, изоляция, параллельность

Единый документ по сквозному (e2e) тестированию Threlium: **зачем** e2e — единственный
автоматизированный gate, **как** устроен harness (Docker Compose + Testcontainers + baked-образ SUT),
и — центрально — **как тесты изолированы** на одном общем WireMock при `pytest -n N`.

Этот документ заменяет прежние `TESTING.md` и `E2E_ISOLATION.md`: они слиты и переосмыслены вокруг одного
стержня — **коррелятора** (см. §2). Связанные контракты — в [INDEX.md](INDEX.md) (storage/fdm/nm_settle/
LightRAG-воркер), [ORCHESTRATION.md](ORCHESTRATION.md) (serial-per-thread, parallel-across-threads),
[PLAYBOOK.md](PLAYBOOK.md) (классы операций, тег `refresh`), [MESSAGES.md](MESSAGES.md) (канонизация MID),
[THREAD_MODEL.md](THREAD_MODEL.md) и [BRIDGE_ISOMORPH.md](BRIDGE_ISOMORPH.md) (тред-идентичность мостов).

> **Терминология «archive».** В коде встречаются два омонима: **bundle archive** (`*.tar.gz` post-deploy,
> артефакт установки) и **mail archive** (историческая выделенная `archive/Maildir` — её больше нет: union
> notmuch root указывает на `stages/`, каждое письмо durable в `stages/<stage>/Maildir/cur/<id>:2,S` после
> `nm_settle()`). Хелперы вида `*_archive*` означают именно «весь тред в union-индексе поверх `stages/`».

---

## 1. Философия: почему e2e — единственный gate

**Политика** ([ARCHITECTURE.md §1.3](ARCHITECTURE.md#13-политика-тестирования)): **единственный**
автоматизированный pytest-gate — e2e в `tests/e2e/`. **Юнит/интеграционных тестов в проекте нет**
(маркер `e2e` навешивается на все `test_*.py` collection-хуком; `tests/unit/` отсутствует намеренно).

**Почему.** Поведение Threlium эмерджентно — композиция `fdm` (`~/.fdm.conf`: `match` → `pipe` →
`notmuch insert … && threlium-dispatch.sh`), `notmuch`, FSM-стадий, RAG-loop **внутри** `threlium-engine`,
мостов и LLM. Единица изоляции в проде — связка submit `threlium-work@` ↔ долгоживущий `threlium-engine`
(handler стадии исполняется **in-process** в движке). Инварианты оркестрации (serial-per-thread, форк треда,
fdm `insert && dispatch`, `threlium-sweep@` backstop) достоверно видны только на живом `systemd --user` в SUT
— их нельзя замокать в юнит-тесте, не потеряв предмет проверки.

**Детерминизм.** e2e проверяет **контур**, а не качество модели: стаб → ответ → журнал/инвариант. Стохастика
реального LLM сломала бы контракт, поэтому **все** LLM/embeddings/мессенджер-API — WireMock-стабы.

**Политика честности (критично).** Тест **не** правит код продукта и **не** проталкивает данные в notmuch/
Maildir внутри SUT (никаких ручных `notmuch insert`, подкладки писем в `new/`, подмены бизнес-логики ради
зелёного assert). Поведенческие таймауты **не** повышают, чтобы «дождаться» медленного контура — чинят стабы/
вход/продукт. Разрешено только **чтение** диска/notmuch для сверки промежуточного состояния. Отсутствие
Docker/Linux/extras `[e2e]` — `pytest.fail(pytrace=False)`, **не** skip: дефект среды не прячут за зелёным.

---

## 2. Стержень: изоляция = коррелятор

Главный урок параллельного прогона (`pytest -n N` на одном WireMock): **изоляция тестов сводится к одному
понятию — коррелятору треда** (`thread-root`). Всё остальное (State-контексты, фазовые защёлки, shared-list,
журнальный guard) — обвязка вокруг него.

**Коррелятор** — это `X-Threlium-Thread-Root`: канонический `Message-ID` **корня** notmuch-треда (старейший
`tag:route`). Каждый исходящий LiteLLM-запрос стадии несёт его в заголовке; стаб матчится по составному имени
контекста `{stub_tag}::{thread-root}`. Два теста с разными thread-root физически не могут cross-match: контексты
`stub-A::root_X` и `stub-B::root_Y` — разные записи в Store.

### 2.1. Три требования к корректному коррелятору

Чтобы изоляция работала на `-n N`, коррелятор теста должен быть одновременно:

1. **Уникальным** — у каждого теста свой; иначе стабы/треды/фазы соседей пересекаются.
2. **Предсказуемым тесту** — тест должен знать значение **до** запроса, чтобы засидить
   `{stub_tag}::{thread-root}` (сид — до того, как SUT сделает первый LLM-вызов; гонки «ingress→первый LLM» нет).
3. **Collision-free по содержимому** — идентичные тела запросов НЕ должны схлопываться в один коррелятор.

Третий пункт — самый коварный и был первопричиной `-n2`-регрессии. Контент-адресуемый MID (`hash(тело)`) даёт
**одинаковый** коррелятор для одинаковых тел → notmuch дедупит/сливает треды → у соседнего теста «исчезает»
glue/тред → unmatched и/или зависание long-hold. Вывод: **при общей notmuch-БД и контент-адресуемых
коррелятах содержимое и запросов, И ответов должно быть test-уникальным** — либо коррелятор не должен зависеть
от содержимого вовсе (см. §2.3).

### 2.2. Стратегия A — контент-адресуемый коррелятор (precompute)

Тест **предвычисляет** thread-root тем же кодом, что и продукт, из тела, которым он владеет:
`ingress_message_id(parent="", tail=<tail>)`. Подходит, когда тест полностью владеет телом (прямой HTTP-клиент):
`thread_root_from_body(surface, body)` ([toolkit/isomorph_cline.py](../tests/e2e/toolkit/isomorph_cline.py)).

**Где ломается** (уроки сессии):
- **Коллизии идентичных тел** (см. §2.1.3): два теста/хода с одинаковым телом → один MID. Лечится только
  test-уникальным содержимым (разные тела + разные ответы-маркеры).
- **Хрупкость реконструкции.** Для реального клиента (Cline) тест **реконструирует** тело, чтобы предвычислить
  MID — и реконструкция зависит от даты/шаблона системного промпта клиента. На границе суток
  `cline_today_mdy()` разъезжается с датой, которую инжектит сам клиент → MID не совпадает → массовый unmatched.
  Это **не** баг продукта, а ловушка хрупкого precompute.

### 2.3. Стратегия B — явный инъецированный коррелятор (`E2E_MID:`) ⭐

Робастная замена precompute. Тест генерит **детерминированный уникальный** MID тем же кодом, что и egress
(`e2e_explicit_root_mid(marker)` → `snowflake_to_mid(hash(marker))` → `<b62@localhost>`), и **кладёт его прямо
в тело запроса** токеном `E2E_MID:<...@localhost>`. Мост в e2e-режиме (`settings.e2e.litellm_route_correlation`)
вынимает токен (`extract_e2e_explicit_mid`, [snowflake_mid.py](../ansible/roles/threlium/files/scripts/threlium/bridges/isomorph/snowflake_mid.py))
и берёт его как ingress thread-root — **без** content-hash. Тест сидит тот же MID. Совпадение гарантировано.

Свойства: уникален (по marker), предсказуем (тест сам выбрал), collision-free (не зависит от тела/даты).
Хелперы — `e2e_explicit_root_mid` / `e2e_root_prompt_token` / `e2e_explicit_root_corr` (inner-форма
pending↔push коррелятора). Это **только** для тестов; прод генерит уникальный MID сам (snowflake), без токена.

**Разделение test/prod.** Прод не предсказуем тесту (snowflake — случайно-уникален), поэтому e2e и прод
разводятся флагом источника MID: прод → snowflake; e2e → `E2E_MID:`/precompute. Механизм продолжения треда
(невидимый водяной знак glue в ответе → `In-Reply-To` следующего хода) **одинаков** в обоих режимах, поэтому
content-режим e2e всё равно прогоняет продакшн-путь — флаг обходит лишь сам генератор MID. Производственная
тред-идентичность мостов — [BRIDGE_ISOMORPH.md](BRIDGE_ISOMORPH.md) / [THREAD_MODEL.md](THREAD_MODEL.md).

### 2.4. Коррелятор LiteLLM на проде vs e2e

Снимок корреляции живёт в **ContextVar** ([litellm_route_context.py](../ansible/roles/threlium/files/scripts/threlium/litellm_route_context.py),
set/reset на границах стадий; дочерние async-задачи наследуют копию). На границе вызова
`merge_litellm_call_kwargs_and_log` ([litellm_client.py](../ansible/roles/threlium/files/scripts/threlium/litellm_client.py))
переносит снимок в `extra_headers`. В снимок входят **только** whitelist-заголовки конверта (`From`, `To`,
`Message-ID`, `In-Reply-To`) + `X-Threlium-Thread-Root` (из корня треда notmuch,
[resolve_route_from_thread_oldest_route_tag](../ansible/roles/threlium/files/scripts/threlium/ingress_route_resolve.py))
+ `X-Threlium-Call-Site` (enum [LitellmCallSite](../ansible/roles/threlium/files/scripts/threlium/types/litellm_call_site.py)).

**Различие.** На проде merge HTTP-заголовков **выключен** (`litellm_route_correlation=false` в
[defaults](../ansible/roles/threlium/defaults/main.yml)); call-site используется лишь для выбора фазы внутри
`llm_func`. В e2e (`group_vars/e2e.yml: threlium_e2e_litellm_route_correlation: true`) заголовки **дополнительно**
подмешиваются для WireMock `hasContext`. Базовый `lightrag_query` штампится **всегда** (прод+e2e) — иначе
`detect_lightrag_call_site_wire` дефолтнул бы к `lightrag_index` и вернул бы `extract_knowledge_graph` вместо
`generate_rag_answer` (wire-мусор в `## Answer`).

### 2.5. Гранулярный `X-Threlium-Call-Site`

Внутри одного thread-root разные LLM-вызовы различаются вторым дискриминатором — `X-Threlium-Call-Site`.
Для LightRAG он вычисляется в рантайме `detect_lightrag_call_site_wire` по сигналам `llm_func`
(`keyword_extraction` / `history_messages` / `system_prompt`, **без** инспекции prompt content) и равен
`function.name` единственного tool (`extract_knowledge_graph` / `…_gleaning` / `summarize_descriptions` /
`extract_query_keywords` / `generate_rag_answer`). Инвариант chat-вызова с одним tool:
`X-Threlium-Call-Site == tools[0].function.name` (исключение — reasoning multi-tool = `reasoning`); проверяется
в `merge_litellm_call_kwargs_and_log`. Offline-аудит контракта стабов — `python scripts/audit_wiremock_tool_stubs.py`.

**Граница гранулярности (важно):** call-site = имя tool-функции, поэтому одна и та же точка вызова в РАЗНЫХ
стадиях НЕ различается (`enrich` и `enrich_fast` зовут один enrich-LLM → оба `enrich_task_plan`). Вводить
синтетический `enrich_fast_*` не нужно: `enrich_fast` — стадия **без своего** LLM-вызова (быстрый rebuild
контекста, «reasoning без повторного RAG»), её «прогон» в стабах вообще не виден. Recovery-петлю
(gate→memory_query→enrich_fast→reasoning) проверяем по **содержимому reasoning** (§3.6.2): весь контекст,
включая recovery-артефакты, попадает в reasoning-промпт. Routing-стадии без LLM (`enrich_fast`,
`egress_router`, `archive`) в call-site списке не представлены — их эффект виден либо в reasoning-контенте,
либо в ответном письме GreenMail.

---

## 3. WireMock State Extension — механизм изоляции

Изоляция держится на [wiremock-state-extension](https://github.com/wiremock/wiremock-state-extension)
(исходники `vendor/wiremock/wiremock-state-extension`; в compose монтируется standalone-JAR в
`/var/wiremock/extensions/`, ServiceLoader, без `--extensions`; нужен `--global-response-templating`).
Расширение хранит **данные** между стабами (properties, shared list) и матчит запросы по состоянию.

### 3.1. Модель данных

| Термин | Описание |
| --- | --- |
| `context` | Имя ключа в Store. В Threlium — составной `{stub_tag}::{thread-root}` или shared (`matrix_rooms`, `telegram_updates`). |
| `state` | Карта `property → value` (строки) на контекст. Повторный `recordState` **мерджит**. |
| `property` | Поле в `state`. Значение `"null"` (строка) **удаляет** свойство. |
| `list` | Упорядоченный список карт в контексте. Только `addFirst`/`addLast`/delete — **in-place правки нет**. |
| `updateCount` | Счётчик изменений контекста (+1 за запрос с ≥1 write). |

Store — `CaffeineStore` (in-memory, lock на весь Store; TTL по умолчанию 60 мин). **Не** распределён; для
параллельных воркеров — **разные контексты** (§2). Шесть расширений одного `ExtensionFactory`:
`recordState`/`deleteState`/`stateTransaction` (ServeEventListener), `state-matcher` (RequestMatcher),
`state` (Handlebars helper), `stateAdminApi`.

### 3.2. `state-matcher` — матчинг по контексту

`customMatcher: {"name": "state-matcher", "parameters": {...}}`. Имя в `hasContext`/`hasNotContext` рендерится
Handlebars **до** проверки (можно `{{request.headers.[x-threlium-thread-root]}}`). Предикаты на контексте:
`hasContext`/`hasNotContext` (есть/нет), `hasProperty`/`hasNotProperty`, `property` (любой `StringValuePattern`),
`list`, `updateCount*`, `listSize*`. Несколько **разных** ключей в одном flat-объекте агрегируются через **AND**.
Логические `and`/`or`/`not` — массивами.

**Базовый стаб LiteLLM:**
```json
{ "request": {
    "urlPathPattern": "^(/v1/chat/completions|/chat/completions)$",
    "headers": { "X-Threlium-Call-Site": { "equalTo": "generate_rag_answer" } },
    "customMatcher": { "name": "state-matcher", "parameters": {
        "hasContext": "stub-<scenario>-01::{{request.headers.[x-threlium-thread-root]}}" } } },
  "response": { "status": 200, "...": "..." } }
```
`stub_tag` (`stub-<scenario>-01`) **захардкожен в JSON** — `upsert` его НЕ подставляет (метаданные ≠ матчер).
Поэтому при переиспользовании каталога стабов другим тестом **сидить нужно тем же зашитым `stub_tag`**, а
изоляция держится на СВОЁМ thread-root (урок SSE-тестов §7).

### 3.3. Фазовый автомат внутри треда — без `priority`

Несколько reasoning-вызовов в одном треде различаются **позиционно** по заголовку
`X-Threlium-Litellm-Req-Seq` (= `thread_len`, сквозной номер вызова в треде): каждый хоп — отдельное
FSM-письмо со своим номером, стаб гейтится `headers: { X-Threlium-Litellm-Req-Seq: { equalTo: "N" } }` +
`X-Threlium-Call-Site`. Заголовок детерминирован, уникален на хоп и стабилен к ретраю (§3.6.8).

> **⚠ ИТОГОВЫЙ подход — `(call-site + req_seq)`, см. §3.6.8.** Легаси: (1) взаимоисключающие стабы
> `hasNotProperty: phase_X` / `hasProperty: phase_X` (негативные предикаты → комбинаторный взрыв,
> ЗАПРЕЩЕНО); (2) промежуточный ПОЗИТИВНЫЙ счётчик фазы `property phase==N` + `recordState phase:=N+1`
> (read-modify-write → НЕ идемпотентен к litellm-ретраю под `-n12`, «кончается» на переменном числе хопов).
> Оба заменены позиционным `req_seq` (не RUW, идемпотентен по построению). `hasNotProperty`/`doesNotContain`/
> `priority` остаются запрещены.

**`priority` запрещён** в сценарных стабах (`tests/e2e/wiremock_stubs/`, кроме `compose_bootstrap/`). Причина:
при `priority` порядок решает число, а не disjoint-state; параллельные фазы становятся непредсказуемы. Если
**два** стаба матчат один запрос одновременно — это ошибка проектирования: WireMock при равном default-priority
(=5) отдаёт **последний зарегистрированный** mapping (`upsert` = remove+add; файлы грузятся по имени — `102_`
позже `100_`). Модель держится на том, что state делает фазы disjoint, а не на `priority`. Также **запрещён**
`doesNotContain`-эксклюзий чужих тестов (допустим лишь для фаз **одного** сценария). Проверка:
`rg '"priority"' tests/e2e/wiremock_stubs/test_` → пусто.

> **Ловушка stale-латч (урок client-disconnect).** Если два теста делят **marker** → делят thread-root →
> делят State-контекст reasoning. `clean_isomorph_test_threads` чистит notmuch, но **не** фазовую защёлку в
> WireMock. Второй тест видит чужой `phase_tasks_ledger_done` → пропускает фазу закрытия задач → finalize-loop
> (open subtasks) → воркер не доходит до idle → teardown зависает. **Лечение: свой marker = свой thread-root =
> свой контекст** (а не reset защёлки задним числом). Multi-turn одного теста (общий контекст с happy-path)
> явно сбрасывает защёлку `wiremock_state_reset_phase` между ходами.

### 3.4. Helper `state` — чтение в ответах (shared list)

В `response-template` (только поле `body`-строка, **не** `jsonBody`): `{{#each (state context='matrix_rooms'
property='list' default='[]')}}…{{/each}}`. Между элементами — `{{#unless @last}},{{/unless}}`. Литеральная `{`
перед `{{#each}}` требует **пробела** (`{ {{#each`), иначе Handlebars видит triple-stache `{{{` →
`HandlebarsException`. Спец-properties: `updateCount`/`listSize`/`list` (весь массив).

### 3.5. Admin API + запись

База `/__admin/state-extension/`. GET `/contexts`, GET/DELETE `/contexts/{name}`, DELETE `/contexts` (все).
**Запись — только через стабы** (PUT/POST в Admin нет); сид — POST на публичный setup-стаб.

> **Gotcha:** `DELETE …/contexts/{name}` с `::`/`<`/`>`/`@` в имени (типичный составной ключ + MID) часто
> **no-op** (204, но контекст остаётся — имя в path не доходит до handler). Точечное снятие property —
> POST-триггеры (`phase_reset`, `recordState` с `"null"`), не Admin DELETE.

### 3.6. On-the-fly запись в state — asserts без зависимости от журнала ⭐

Расследование исходников (`vendor/wiremock/wiremock-state-extension`, `vendor/wiremock/wiremock`): стаб
может **на лету, во время обслуживания запроса**, считать и записать в state счётчики/флаги/выжимки —
так тест проверяет инвариант по **state** (читая его probe-стабом, см. §3.6.1/§3.6.2 — Admin
`GET /contexts/{name}` ломается на спецсимволах thread-root `::`/`<`/`>`/`@`, §3.5), а не сканируя журнал.
Это снимает зависимость от **объёма журнала** (кумулятивен за сессию, ring-buffer
`--max-request-journal-entries`, см. §9) и от per-tag чисток.

Как это работает (`RecordStateEventListener.beforeResponseSent`): значения `state`/`list` в `recordState`
**рендерятся Handlebars** по модели `request` + `response` (`{{jsonPath request.body …}}`,
`{{request.headers.…}}`), а `TemplateEngine` WireMock регистрирует jknack-хелперы `ConditionalHelpers`
(`eq`/`gt`/`and`/`or`/`not`), `NumberHelper`, `StringHelpers` + `contains`/`size`/`val (assign)` и сам
helper `state` (чтение текущего контекста). То есть прямо в `recordState` доступны и текущее состояние, и
данные запроса.

Идиомы (вместо «просканировать журнал по `stub_tag`»):
- **Счётчик попаданий** без арифметики: на каждый матч `list: { addLast: { hit: "1" } }` → число =
  `listSize` (special-property; читается probe-стабом `…/state/list_size`, не Admin path — см. §3.6.1).
- **Захват/выжимка**: `state: { last_chat_id: "{{jsonPath request.body '$.chat_id'}}" }`.
- **Assert на лету как флаг**: `state: { saw_needle: "{{#if (contains request.body 'NEEDLE')}}1{{/if}}" }`
  или сравнение через `eq`/`gt`; тест читает флаг из контекста.
- **Чтение в ответе/следующей фазе**: helper `{{state context='…' property='listSize'}}` (или `property`/
  `list`); спец-свойства — `updateCount`, `listSize`, `list`.

Ограничения: `recordState` — `serveEventListener` на **сматченном** стабе → срабатывает только когда стаб
матчится (unmatched в state не попадёт — для «чужого/неожиданного трафика» остаётся journal unmatched-guard
§5); запись идёт `beforeResponseSent` (после решения о матче) → влияет на **следующий** запрос, не на свой
(паттерн фаз §3.3). Рекомендация: где assert сейчас зависит от полноты журнала (подсчёт LLM-POST, поиск по
`needle`), переноси на state-счётчик/флаг — устойчивее на `-n2`.

### 3.6.1. Единый call-site recorder + state-asserts (итоговое состояние) ⭐

**Целевая архитектура проверок** (uniform, единый подход): весь жизненный цикл сценария наблюдаем по
вызовам моделей в WireMock, поэтому проверяем по **state**, а наружу ходим только в **GreenMail** (финальное
письмо). Никаких `docker exec` (`service_exec`) в SUT для ассертов, никакого скана журнала, никакой изоляции
по `stub_tag` — только коррелятор-заголовок `X-Threlium-Thread-Root` (§2).

- **Static call-site recorder.** Каждый сценарный LLM-стаб (`chat/completions` + `embeddings` со
  `state-matcher`) СТАТИЧЕСКИ несёт листенер
  `recordState → list.addLast { cs: "{{request.headers.[X-Threlium-Call-Site]}}" }` в контекст,
  ключёванный ЧИСТО по `{{request.headers.[X-Threlium-Thread-Root]}}` (tag-free). Так в state копится
  **упорядоченный список call-site всего треда** (`ingress_distill → enrich_* → lightrag_* → reasoning →
  summarize_thread_context → …`). Листенер — **в JSON стаба** (не инъекция в рантайме: динамическая
  правка/генерация стабов запрещена, §6.4; «динамика» = статический recordState).
- **Чтение — probe-стабы `compose_bootstrap/` (helper `state`, не Admin path):**
  `POST /__threlium/e2e/state/call_sites` → `{"call_sites":[…]}` (helper
  `wiremock_state_thread_root_call_sites`), `…/state/property`, `…/state/list_size`.
- **Все прежние journal/docker-exec проверки выводятся из списка:** число LLM-POST = `len`; покрытие стадии
  = `cs in call_sites`; summarize-count = `count('summarize_thread_context')`; lightrag-indexed =
  `'lightrag_index' in call_sites` (заменил `docker exec stat` глобального faiss — тот под `-n2` голодал на
  конкуренции docker-exec, см. §9). Терминальные стадии без LLM (`egress_router`/`egress_email`/`archive`)
  подтверждаются **ответным письмом GreenMail**, а не стабом.
- **Идеал (финальная фаза) — РЕАЛИЗУЕТСЯ через §3.6.6, НЕ через наивный «снять префикс + грузить всё».**
  ⚠ Прямой «drop `{stub_tag}::` + load-all-dirs» доказан ВНУТРЕННЕ ПРОТИВОРЕЧИВЫМ: один и тот же call-site
  (напр. `075_chat_ingress_distill`) лежал в десятках каталогов, развязанных ТОЛЬКО композитным префиксом;
  снять префикс + накопить → N-кратный match (предикат `hasContext` проверяет лишь существование контекста).
  **Рабочий путь — слияние дублированных call-site в один статический generic-bootstrap-стаб с ПОЗИТИВНЫМ
  гейтом `hasContext(thread-root)+property active==1` (§3.6.6).** По мере слияния call-site'ов исчезает
  per-test churn (и его `404`-окно), а `stub_tag` ретайрится **инкрементально, где вытеснен generic'ом**
  (контент-зависимые opt-out-стабы законно держат композитный `hasContext` — полный tag-free это далёкий
  end-state, не big-bang). Итог-вектор тот же: статика стабов + state-extension, изоляция по thread-root,
  наружу — только GreenMail+WireMock.

### 3.6.2. Проверка СОДЕРЖИМОГО — content-flags (а не журнал, не bodyPatterns+guard) ⭐

call-site список (§3.6.1) даёт **счёт/наличие** стадий. Проверку **содержимого** запроса (попал ли нужный
текст в промпт LLM) делаем **content-flag**: статический `recordState` на стабе на лету вычисляет
`contains` по телу и пишет флаг в state; тест читает флаг probe-стабом `/state/property`.

**Почему content-flag, а НЕ bodyPatterns+unmatched-guard** (рассмотрены оба):
- bodyPatterns+guard **скрывает «где упало»**: неверное содержимое → стаб не сматчился → unmatched → падает
  *guard* обобщённо (часто в другом тесте/в конце, каскадом), а не «маркер X отсутствует».
- правка Jinja2-промпта при bodyPatterns **ломает контур**: стаб перестаёт матчиться → у LLM-вызова нет
  ответа-заглушки → FSM висит/падает (а не просто ассерт). content-flag оставляет матчер мягким (контур жив),
  падает на конкретном ассерте — та же диагностируемость, что у прежнего journal-скана, но дёшево из state.

**Идиомы:**
- **Наличие:** `"saw_X": "{{#if (contains request.body 'MARKER')}}1{{else}}…{{/if}}"`; тест: `saw_X == "1"`.
- **Sticky** (для multi-hop: флаг не сбрасывать следующим вызовом без маркера) — в `{{else}}` читаем текущее
  значение: `{{state context=request.headers.[X-Threlium-Thread-Root] property='saw_X' default='0'}}`.
  **⚠ Только для НЕ-конкурентных стадий** (reasoning/summarize — последовательны на handler-треде). На
  КОНКУРЕНТНЫХ вызовах (lightrag-drain: много embed'ов параллельно на общий контекст) sticky read-modify-write
  ТЕРЯЕТ записи — см. §3.6.3, там concurrency-safe append-only вместо sticky.
  **ЗАМЕРЕНО (2026-06-15, живой контейнер):** (A) 24 контекста параллельно, sticky/regexExtract-флаги —
  изоляция ДЕРЖИТСЯ (нет перекрёстного загрязнения); (C) 24 контекста × N ПОСЛЕДОВАТЕЛЬНО, контексты
  параллельно — счётчики корректны; (B) 100 CONCURRENT инкрементов в ОДИН контекст — потеряно ~18 (глобальный
  замок сериализует ЗАПИСЬ, но eval шаблона/чтение — ДО замка → read-modify-write НЕ атомарен на одном
  контексте). ⟹ sticky-флаги и `math`-счётчики БЕЗОПАСНЫ для параллельного прогона тестов (разные тесты =
  разные thread-root = разные контексты) И для последовательного reasoning одного треда; ОПАСНЫ только при
  concurrency на ОДНОМ контексте (чего у reasoning нет; для конкурентных стадий — append-only, §3.6.3).
- **Несколько вариантов:** `(or (contains … 'H1') (contains … 'H2') …)` (jknack `or` — вариадик).
- **Отсутствие (нет negative matchers — они хрупки):** позитивный флаг «forbidden present» + ассерт `== "0"`.
- **СЕКЦИОННАЯ проверка (маркер обязан быть ВНУТРИ конкретной секции, напр. `<conversation_history>`, а не в
  `<conversation_delta>`) — `regexExtract` ⭐ (ЗАМЕРЕНО на контейнере 2026-06-15).** WireMock-core хелпер
  `regexExtract` с 1 аргументом возвращает совпадение ИНЛАЙН (как подвыражение): извлекаем секцию и ищем
  маркер ВНУТРИ неё —
  `"{{#if (contains (regexExtract request.body '(?s)<conversation_history>.*?</conversation_history>') 'MARKER')}}1{{else}}…{{/if}}"`.
  `(?s)` = DOTALL (многострочно). Проверено: маркер в delta (не в history) → флаг `0`; маркер в history →
  `1` — точность «внутри секции» СОХРАНЕНА (раньше это давал только journal-скан; теперь без журнала и без
  хрупкости). Отсутствие-в-секции = `contains(regexExtract …, FORBIDDEN)` + ассерт `== "0"`. Это
  предпочтительный путь миграции journal-структурных проверок (unified_context) — не давать «разные токены»,
  а извлекать секцию. Экранирование `{n}`-квантификаторов в JSON: `\\{n\\}` (см. vendor/wiremock/EXAMPLE.md).
  **⚠ СКОУП на нужное сообщение (проверено 2026-06-15):** `regexExtract` по ВСЕМУ `request.body` ловит
  false-positive, если СИСТЕМНЫЙ промпт УПОМИНАЕТ тег (`<conversation_history>` как инструкцию формата) —
  non-greedy спан от упоминания в system до закрытия в user захватывает чужой контент (envelope с
  `enrich_fast`). Решение: сначала `jsonPath` на нужное сообщение, потом regexExtract —
  `(regexExtract (jsonPath request.body '$.messages.[-1].content') '(?s)<conversation_history>.*?</conversation_history>')`
  (`[-1]` = последнее = user-ход). Это ТОЧНО скоуп оригинального journal-скана (user-content), не хрупко.
  unified_context так зелёный -n0, без журнала. **То же для проверки НАБОРА ТУЛОВ** (gated reasoning не
  предлагает `response_finalize`): системный промпт УПОМИНАЕТ имя тула даже когда гейт убрал его из массива →
  скоупь на сам массив: `(contains (jsonPath request.body '$.tools') 'response_finalize')` (проверено;
  technical_gate так зелёный). Правило: проверяешь структурный факт (в какой секции/поле) — сначала
  jsonPath/regexExtract на нужное поле, потом contains; whole-body даёт false-positive от промпта.

**Time-independent чтение (не поллинг — поллинг = риск flaky):** читаем флаг ПОСЛЕ существующего
happens-before барьера. Контентные ассерты идут после `assert_full_mailflow_pipeline` (ждёт ответ GreenMail =
контур завершён), а reasoning/summarize отрабатывают причинно ДО egress→ответа (`recordState`
beforeResponseSent) → флаг уже записан → **прямое чтение**, без таймаута. Поллим только истинно
асинхронное (напр. `lightrag_index` drain, отстающий от ответа).

**Матрица «что чем проверяем» (итог):** счёт/наличие стадий → call-site список (§3.6.1); содержимое промпта →
content-flag; egress транспорта (telegram/matrix) — это тоже WireMock-вызов с thread-root → content-flag на
egress-стабе (как GreenMail для почты); финальная доставка почты → GreenMail; целостность (чужой трафик в
пустоту) → journal **unmatched-guard** (§5) — единственное оставшееся использование журнала. `docker exec`
(`service_exec`) — только setup/cleanup/deploy/restart и failure-diag, НЕ в проверках.

**Паттерн миграции спец-потоков (live: cli / subagent / hitl / memory / reflect) — per-flow content-flag.**
Прежние notmuch routing-проверки (`poll_notmuch_thread_in_stage_folder` + `assert_notmuch_thread_has_messages_
in_folders`, docker-exec) подтверждали лишь СУЩЕСТВОВАНИЕ Maildir-стадий. Заменяем на: **content-flag
УНИКАЛЬНОГО маркера потока, дошедшего до reasoning** (строго сильнее «папка стадии есть» — доказывает, что
результат вернулся в контур), плюс ответ GreenMail (исход) плюс фазовые стабы (`hasProperty …_ledger_done` на
egress) + unmatched-guard (маршрут fail-closed). Пример (cli_intent_allow_echo): `cli_exec` echo
`e2e-cli-allow-xyzzy` → content-flag `saw_cli_echo` на post-cli reasoning-стабе; тест читает флаг ПОСЛЕ ответа
(time-independent). Маркер контролирует тест (он задаёт и инжект, и стабы): subagent → результат субагента в
reasoning; memory → memory-note в reasoning; hitl → артефакт resume; reflect → reflect-вывод. Routing-стадии
без LLM (`cli_exec`/`enrich_fast`/`egress_router`/`archive`) в call-site списке не видны — их эффект ловим
именно этим content-flag или ответным письмом, не notmuch-папкой.

### 3.6.3. Per-test флаг на ОБЩЕМ стабе под КОНКУРЕНЦИЕЙ — статический маркер + append-only ⭐

Когда проверку нельзя положить на СВОЙ стаб теста, потому что сматченный стаб **общий, кросс-тестовый**
(bootstrap-«случай, который не разделить» — напр. `011_embeddings_generic_index`: drain КАЖДОГО теста шлёт
`lightrag_index`; свой стаб у теста невозможен — `priority` в сценарных стабах запрещён, развести нечем) —
общий стаб несёт **личный счётчик теста**: статически вычисляет нужный маркер и пишет его в контекст,
ключёванный по тесту; ОСТАЛЬНЫЕ тесты этот счётчик просто не читают.

**⚠ Грабли конкуренции (расследовано 2026-06-10 на `test_lightrag_index_filter_e2e`).** Наивный
«seeded-marker + sticky-флаг» (тест динамически сидит `search_for`, общий стаб `contains`-ит его и
sticky-пишет `saw_match` через read-modify-write) **разваливается под параллельным lightrag-drain'ом**.
Три независимо доказанные причины (изолированные пробы прямым POST на живой WireMock, см. §3.6.4):
1. **Thread-root HEADER не доходит до lightrag-вызовов надёжно.** Корреляция в lightrag идёт через ctxvar,
   а HTTP-заголовок `X-Threlium-Thread-Root` под внутренней параллельностью lightrag (общий rag-loop, конкурентные
   задачи/батчи) теряется/перемешивается. Значит `recordState.context = {{request.headers.[X-Threlium-Thread-Root]}}`
   уезжает в пустой/чужой контекст. Надёжный коррелятор lightrag-вызова — **`regexExtract` Message-ID ИЗ ТЕЛА**
   (он в чанке/запросе всегда есть). [Это разворачивает прежнюю рекомендацию «ключ = заголовок».]
2. **Динамический `seed` гонится с embed'ами.** `search_for` сидится перед контуром, но конкурентные embed'ы
   фаерят РАНЬШЕ коммита seed → читают `default` → маркер «не виден» (в пробе: seed стабильно коммитился
   ПОЗЖЕ всех 40 embed'ов → флаг всегда `0`/пусто).
3. **Read-modify-write теряет записи.** `recordState` на общий контекст не атомарен: sticky-флаг
   (`{{else}}{{state … property='saw_match'}}{{/if}}`) и даже `list.addLast` ТЕРЯЮТ записи под контеншном
   (в пробе: cs 37–50 из 50; sticky `1` затирался конкурентным `0` в 1 из 6 прогонов). Крайний случай —
   inner `state`-read под контеншном отдаёт неконсистентное → `contains` бросает → `handleState` падает →
   property не пишется вовсе (`/state/property` отдаёт probe-default `'error'`), хотя `list` соседнего
   листенера всё равно лёг (отсюда «cs есть, флага нет» — внешне «невозможное» состояние).

**Concurrency-safe идиома (рабочая): статический маркер + append-only в ВЫДЕЛЕННЫЙ контекст.**
- Маркер **СТАТИЧЕН в стабе** (хардкод), не сидится тестом → seed-гонки (2) нет, и в `#if` НЕТ inner
  `state`-read → нет источника throw (3-крайний).
- Контекст ключуется по **body-corr** (`regexExtract` Message-ID), не по заголовку (1).
- Запись — **`list.addLast` в контекст, куда пишет ТОЛЬКО маркер-embed**: при совпадении — `forbidden-index-<corr>`,
  иначе — junk-контекст `_no_forbidden_marker`. В целевой контекст пишет лишь ОДИН embed → **единственный
  писатель, нет read-modify-write гонки** (3 неприменимо). Тест читает `…/state/list_size`:
  ```
  context = {{#if (contains request.body 'To: ingress@localhost')}}forbidden-index-{{regexExtract request.body '<[A-Za-z0-9]{40,}@localhost>' default='_nocorr'}}{{else}}_no_forbidden_marker{{/if}}
  list.addLast = { "hit": "1" }
  ```
- Тест: `list_size("forbidden-index-" + correlation_key) == 0` (для no-history письма body-corr чанка ==
  его собственный Message-ID == thread-root). `0` = не проиндексировано (PASS); `>0` = drain зря
  проиндексировал (FAIL). **Отсутствие/наличие append-only — concurrency-safe** (потеря записи дала бы лишь
  ложный PASS у НЕГАТИВНОГО ассерта, а ложный hit исключён: junk не читается).

Пример: `test_lightrag_index_filter_e2e` — индексируемый чанк несёт `To: ingress@localhost` (MIME-заголовок),
query-embed его не несёт → у не-проиндексированного no-history письма hit'ов 0. Изоляционные пробы под 50
конкурентных серверов: NOT-indexed → `0` (6/6), INDEXED → `1` (6/6); валидировано -n0 (green) и -n2.
Маркер `To: …` НЕ уникален между тестами — изоляцию даёт body-corr контекст, чужие hit'ы лежат в чужих
`forbidden-index-<MID>` и никем не читаются.

**Отладка Handlebars — прямой WireMock-скрипт, без 40с e2e-прогонов.** Регистрируем на ЖИВОМ e2e-WireMock
(`/__admin/mappings`) диагностический стаб, чей ОТВЕТ (`response-template`) ЭХАЕТ выражения по-кусочно
(`A=[{{regexExtract …}}] B=[{{state context=… property=…}}] …`) ИЛИ `recordState`+read-back-probe; POSTим
контролируемые тела/заголовки, читаем рендер за секунды. Так изолируем баг Handlebars от проблем самого теста
(именно так найдено: все подвыражения исправны, а `saw_match=''` — от неверного context-ключа, не от шаблона).
`state`-helper доступен и в response-template (`StateTemplateHelperProviderExtension`), и в `recordState`
(значения `list`/`state` идут через `renderTemplate` с полным набором helper'ов). Скрипт — одноразовый,
cold-reset следующего прогона стирает диагностические стабы.

### 3.6.4. Отладка стабов и наблюдаемость ошибок — доступные инструменты ⭐

Чем ловить баги динамических стабов (Handlebars в `recordState`/response-template), не угадывая по 40с-прогонам:

1. **Прямой WireMock-скрипт (харнесс)** — на ЖИВОМ e2e-WireMock (`POST /__admin/mappings`) регистрируем
   диагностический стаб, чей ОТВЕТ (`response-template`) ЭХАЕТ выражения по-кусочно
   (`A=[{{regexExtract …}}] B=[{{state context=… property=…}}] …`) ИЛИ `recordState`+read-back-probe; POSTим
   контролируемые тела/заголовки `curl`'ом, читаем рендер за секунды. Изолирует Handlebars от теста.
2. **Response-template сюрфейсит ПОЛНЫЕ исключения** — ошибка компиляции/рендера → HTTP 500 с сообщением и
   `^`-позицией (`could not find helper: 'quote'`). Самый быстрый способ поймать parse-ошибку шаблона.
3. **Helper `handleError` → строка-ошибка inline** — `state` (`'context' cannot be empty`; property+list
   вместе), `regexExtract` (`Nothing matched …` без `default`) **возвращают строку-ошибку**, а не молчат.
   В `recordState` она становится значением property → видна probe'ом; в response — в теле.
4. **Трёхзначное чтение property** — probe `003` дефолтит **`'error'`** (не `''`): `'1'`/`'0'`=записанные
   значения, **`'error'`=property не записана** в контекст → recordState не сработал ИЛИ context-ключ
   разошёлся (wiring-баг, не регрессия продукта). Так `''`-молчание заменено явным сигналом.
5. **`updateCount` special-property** — `{{state context=X property='updateCount'}}` = сколько раз писали
   контекст (liveness: `0` ⇒ ни один `recordState` контекст `X` не тронул — стаб не сматчен / ключ не тот).
6. **Journal `/__admin/requests`** — реальные тела/заголовки, что РЕАЛЬНО попало в стаб. Так найдено:
   `regexExtract '<[A-Za-z0-9]{40,}@localhost>'` берёт первый `<…@localhost>` = **Message-ID чанка**, а не
   thread-root; надёжный ключ thread-root — заголовок **`X-Threlium-Thread-Root`** (`== correlation_key`).

**Авто-capture исключений `recordState`→контекст ОТСУТСТВУЕТ** (`beforeResponseSent`→`run()` без try/catch;
исключения идут в `notifier()` = docker-logs, не в state) и **намеренно НЕ добавляется** (правка vendored-
`RecordStateEventListener`). Для исключений — харнесс (п.1–2); для wiring/absent — п.4 (`'error'`) и п.5.

### 3.6.5. LightRAG retrieval включён suite-wide — контракт embed-стаба + калибровка ⭐

Весь e2e-сьют исторически калибровался вокруг **пустого** retrieval (причина ниже), поэтому включение
реального retrieval — это **suite-wide** смена калибровки, а не локальная правка одного теста. Эти инварианты
**соблюдать при любой правке embed-стабов**.

- **Embed-стаб: батч-контракт (баг `@first`, найден 2026-06-12).** Стаб эмбеддингов рендерит вектор по 1536
  измерениям через `{{#arrayJoin ',' (range 0 1535) as |d|}}…{{/arrayJoin}}` для **каждого** входа батча
  `{{#each r.input as |txt|}}…{{/each}}`. LiteLLM/LightRAG шлёт embed **батчем** (`[query, ll_keywords,
  hl_keywords]` на retrieval, до N чанков на index). **Ловушка:** Handlebars `@first` ВНУТРИ `arrayJoin`
  резолвится против ВНЕШНЕГО `{{#each r.input}}`, не против итерации измерения — `{{#if @first}}<val>{{else}}0.0
  {{/if}}` отдавал вектор только 1-му входу батча, входам 2..N — все-нули. LightRAG берёт `ll_embedding =
  all_embeddings[1]` (2-й вход) → нулевой query-вектор → LanceDB на вырожденном векторе возвращает пусто →
  hybrid `_build_query_context` → `None` → `generate_rag_answer` НЕ файрился. **Контракт: вектор рендерится
  ОДИНАКОВО для всех входов батча — никакой `@first`/позиционной логики по входу.** Идиома:
  `{{#arrayJoin ',' (range 0 1535) as |d|}}<val>{{/arrayJoin}}` (один `<val>` на каждое измерение и каждый вход).
- **Почему включение retrieval = suite-wide.** При сломанном retrieval `generate_rag_answer` /
  `lightrag_query_rerank` почти никогда не файрились → у большинства тестов **нет** стабов под них. Починка
  embed'ов включает retrieval на КАЖДОМ enrich по всему сьюту → эти вызовы начинают файриться в ~70 контурах →
  нет матча → unmatched-лавина → глобальный guard (§5) падает. **Калибровка:** generic catch-all
  `generate_rag_answer` + `rerank` стабы в `compose_bootstrap/` (как generic KG-стабы 012/013/014), плюс
  per-test стаб только там, где тест ассертит конкретный контент.
- **Кросс-стаб косинус.** Векторы во ВСЕХ стабах согласованы по форме: при коллинеарных векторах cosine=1.0
  везде (retrieval тянет top_k «всего»); при произвольно-разных может упасть ниже lightrag-дефолта
  `cosine_better_than_threshold` (0.2) → retrieval не находит ничего. Любая попытка сделать векторы
  **per-контент различимыми** (чтобы retrieval был точечным) обязана сохранить инвариант: вектор одного и того
  же контента воспроизводим (query keywords ↔ indexed chunk), а cross-контент cosine > threshold там, где
  retrieval ДОЛЖЕН найти. ⚠ **Открытый трейдофф латентности (под расследованием):** коллинеарные uniform-векторы
  → retrieval тянет top_k «всего» → раздутый reasoning-контекст → +нагрузка на единый rag-loop → тяжёлые
  контуры (matrix `full_contour`, `formal_reason`) рискуют не успеть за поведенческий poll. Доктрина §1/§5:
  таймаут = скрытый стопор, не «нагрузка» — измерять стадии контура, не бампить таймаут.
- **Retrieval-параметры** (`settings.lightrag.query_*`, дефолты): `query_top_k=40`, `query_chunk_top_k=20`,
  `query_max_entity_tokens=6000`, `query_max_relation_tokens=8000`, `query_max_total_tokens=30000`,
  `query_mode=hybrid`, `query_api=aquery_llm` (включает финальный `generate_rag_answer`), `enable_rerank=True`.

### 3.6.6. Слияние дублированного call-site в ОДИН generic-bootstrap-стаб (позитивная фильтрация) ⭐⭐

**Проверенный шаблон детега (commit `7117065`, валидирован `-n4`).** Когда один и тот же call-site
реализован **дублированными per-test стабами** с одинаковым (или boilerplate) ответом — это источник
flake под нагрузкой. Пример: `ingress_distill` лежал в 51 каталоге; ответы структурно идентичны (различался
лишь `user_intent`/`reply_language`, нигде не ассертится). Per-test стаб на ОБЩИЙ call-site означает, что
каждый тест **ре-апсертит** (WireMock `editMapping` = remove+add окно) и per-test **выгружает** его; под
`-n4` in-flight вызов соседа ловит окно отсутствия → `404` → краш воркера (`Restart=no`) → застряло
`unread` → нет ответа GreenMail → reply-timeout. Это и был доказанный корень flake `lightrag_correlator`
(3 теста делят `LIGHTRAG_INTEGRITY_SPEC`).

**Решение — слить дубли в ОДИН статический стаб в `compose_bootstrap/`** (грузится однократно за сессию,
никогда не churn'ится — как существующие generic 011/012/013/014/015). Развязка и изоляция — **строго
позитивная**:

- **Гейт стаба** = заголовок-контракт продукта `X-Threlium-Call-Site: <site>` + state-matcher
  `hasContext({{request.headers.[X-Threlium-Thread-Root]}})` + `property { active: { equalTo: "1" } }`.
- **`prepare_wiremock_scenario` сеет ЧИСТЫЙ thread-root** контекст (`active=1`) для **opt-in** сценариев.
  Call-site recorder создаёт этот контекст лишь **во время обслуживания** (слишком поздно для матчера на
  ПЕРВОМ вызове), поэтому выделенный флаг `active` (который recorder НЕ ставит) — чистый позитивный
  дискриминатор «тест активен и хочет generic».
- **Opt-out — АВТОМАТИЧЕСКИЙ, без per-test учёта:** каталог, несущий СВОЙ `*<site>*`-стаб, сам обслуживает
  этот call-site и НЕ сеется `active=1` → generic-стаб остаётся инертным (нет double-match). Так
  **контент-зависимые** ответы держат свой стаб: distill, чей `user_intent`/размер реально рулит контуром —
  summarize (filler→overflow), fsm (initial+recovery), context-trim (warmup/turn2), live-маршруты
  cli/subagent/memory/reflect/hitl (`user_intent` выбирает спец-маршрут), а также isomorph/telegram/matrix
  (свой prepare-path, `active=1` не сеют). `recordState` пишет call-site в thread-root контекст как и
  раньше → `call_sites`-ассерты не меняются.

**HARD-ПРАВИЛА развязки (почему именно так, а не иначе) — нормативны:**
1. **НЕТ `priority` нигде** (включая `compose_bootstrap/`): порядок-по-числу ≠ disjoint-state, делает
   параллельный матчинг непредсказуемым (см. §3.3). Инвариант: `rg '"priority"' tests/e2e/wiremock_stubs` → пусто.
2. **НЕТ `hasNotProperty`** для развязки generic↔спец-стаб: ведёт к **комбинаторному взрыву** (generic
   пришлось бы перечислять КАЖДЫЙ спец-случай). Только позитивный opt-in (`property active==1`).
3. **НЕТ cross-test `doesNotContain`** (допустим лишь между фазами ОДНОГО сценария, §3.3).
4. **НЕ матчить по jinja2-генерируемым частям тела** запроса (промпт меняется при правках продукта → хрупко,
   §3.6.2). Матчить только по тому, что **контролирует тест** (письма, тела LLM-ответов, засеянный state) +
   заголовок-контракт `X-Threlium-Call-Site`.
5. **Файлы стабов статические, БЕЗ jinja2-генерации** файлов. Но WireMock `response-template` (Handlebars
   ВНУТРИ статического стаба) — **разрешён и ключевой** (echo/`state`/`regexExtract`), это не генерация файла.

**Окно `404` бьёт ТОЛЬКО по shared+churn'енным стабам.** Уникальный per-test стаб безопасен: per-test
drain-барьер (§3.6.1 / conftest) дожидается простоя контура ПЕРЕД выгрузкой. Поэтому generic-merge —
структурное **снятие кросс-тестового шаринга** дублированных call-site; «load-once + no per-test unload»
нужен лишь как остаточный fallback для genuinely-shared-non-generic стабов (если такие переживут слияние),
а НЕ как глобальный флип.

### 3.6.7. State-флаги НЕЗАВИСИМЫ от журнала — как обойтись без journal-чтения ⭐⭐

**Container-proven (2026-06-14, прямой прогон на живом WireMock `state-extension`).** State-расширение
хранит данные в `CaffeineStore`, **полностью отдельном** от request-журнала. Доказано опытом: создать стаб
с append-only листенером (`recordState → list.addLast`), вызвать его, затем `DELETE /__admin/requests`
(полная очистка журнала) — и прочитать контекст. Контексты до и после очистки **байт-в-байт идентичны**
(содержимое `list` и `updateCount` не меняются). Журнал эфемерен (кумулятивен за сессию, вытесняется
ring-buffer'ом §5/§9); **контекст State — durable-запись**. Вывод: **читать журнал для ассертов не нужно —
всё, что раньше доказывал journal-скан, выражается per-test state-флагом.**

**Один generic-стаб → личные непересекающиеся флаги тестов (тоже проверено).** Один общий стаб в том же
прогоне писал СРАЗУ в три контекста: фиксированный + два body-corr (`{{regexExtract request.body
'TEST-[A-Za-z0-9]+'}}`). Два «теста» (разные маркеры тела) держали КАЖДЫЙ свой `list` в СВОЁМ контексте,
counts точны (2 и 1) — пересечения нет. Это и есть смысл generic-стаба: **стаб один, флаги у тестов свои**
(§3.6.3). `regexExtract` вытащил маркер теста и в запись листа, и в имя контекста-ключа — стаб реагирует на
СВОЁ зарегистрированное тестом тело.

**Рецепт миграции journal-by-stub_tag ассерта → state-флаг (нормативный для детега):**
- **Было** (хрупко, зависит от журнала + `stub_tag`): тест зовёт `find_wiremock_requests_by_body_contains(
  wm, MARKER, stub_tag=...)` и считает совпадения в журнале. Ломается при generic-merge (у generic-стаба
  свой `stub_tag`/нет тестового) и при ring-buffer-вытеснении.
- **Стало:** generic-стаб СТАТИЧЕСКИ вычисляет нужный сигнал и пишет его append-only в per-test контекст;
  тест читает `…/state/list_size` (или property) probe-стабом. Пример (эквивалент memory-query
  «embedding нёс query-marker»):
  ```
  recordState.context = {{#if (contains request.body 'E2E-MEMORY-QUERY-MARKER')}}saw-qmarker-{{regexExtract request.body '<[A-Za-z0-9]{40,}@localhost>' default='_nocorr'}}{{else}}_no_qmarker{{/if}}
  recordState.list.addLast = { "hit": "1" }
  ```
  тест: `list_size("saw-qmarker-" + <свой body-corr/thread-root>) >= 1`.

**Чем ключевать контекст (приоритет сигналов — итог §2/§2.5/§3.3/§3.6.3):**
1. **Структура + thread-root-заголовок (основной).** `X-Threlium-Call-Site` (какой вызов) + порядок через
   фазовые props (§3.3) + контекст по `{{request.headers.[X-Threlium-Thread-Root]}}`. Надёжно для вызовов
   на handler-треде (ingress_distill/enrich/reasoning/summarize/query-side) — заголовок там доходит.
2. **Body-corr (вторичный, но ОБЯЗАТЕЛЬНЫЙ там, где заголовок теряется).** Для lightrag-внутренних вызовов
   (index/KG-drain, батч-embed) `X-Threlium-Thread-Root`-заголовок НЕ доходит (§3.6.3 п.1, ctxvar-корреляция)
   → ключ по `regexExtract` Message-ID/маркера ИЗ ТЕЛА. Это не «запасной хрупкий» вариант, а единственно
   надёжный для этих вызовов; для остальных — да, вторичный.
- **Конкурентность:** только **append-only single-writer** в выделенный контекст, НЕ read-modify-write sticky
  (теряет записи под параллельным drain'ом — §3.6.3 п.3). Sticky read-modify-write допустим лишь на
  ПОСЛЕДОВАТЕЛЬНЫХ handler-тред стадиях (reasoning/summarize, §3.6.2).

**Что у журнала ОСТАЁТСЯ (не путать).** Единственное легитимное использование журнала — **глобальный
unmatched-guard целостности** (`GET /__admin/requests/unmatched` пуст, §5): это инвариант «нет leak'нувших
запросов», а не per-test content-чтение, и он остаётся. Миграция journal→state касается **только
content/count-ассертов** конкретных тестов.

### 3.6.8. Многошаговые reasoning-тесты — `(call-site + req_seq)` вместо фазовых счётчиков ⭐⭐

**ИТОГОВЫЙ подход (2026-06-17).** Каждый reasoning-хоп адресуется парой
**`(X-Threlium-Call-Site, X-Threlium-Litellm-Req-Seq)`** — позитивный header-матч, без state-RUW. Это
заменяет всё промежуточное: фазовый счётчик (`property phase==N` + `recordState phase:=N+1`), retry-дубликаты
стабов и absorbing-`207`/авто-паддинг — они **обсолетны**.

**`X-Threlium-Litellm-Req-Seq = thread_len` (сквозной номер вызова в треде).** Продукт на КАЖДОМ LLM-вызове
ставит этот заголовок = длина IRT-цепочки треда на момент стадии: `runners/engine/fsm.py:_run_stage` берёт её
из УЖЕ кэш-материализованной цепочки (`stage_materialization_cache` активен — БЕЗ отдельного notmuch-обхода) и
кладёт во внутренний слот корреляции; `litellm_client._assign_litellm_request_seq` отдаёт это значение в wire.
Свойства:
- **детерминировано** для фиксированного сценария (FSM-путь фиксирован; тред изолирован по thread-root →
  параллелизм `-n12` не влияет);
- **уникально на каждый reasoning-вызов** в треде (каждый хоп = отдельное FSM-письмо → растущий `thread_len`);
- **стабильно к ретраю** (идемпотентно ПО ПОСТРОЕНИЮ): litellm повторяет HTTP уже посчитанными заголовками, а
  worker-restart переигрывает стадию с тем же `thread_len` → тот же seq → тот же стаб → тот же ответ;
- **в письмо НЕ пишется** (только в исходящий LLM-запрос), нигде не персистится — хранить/передавать не нужно.

**Механика хопа (нормативная, БЕЗ state-RUW):**
- **Гейт:** `headers: { "X-Threlium-Call-Site": { equalTo: "reasoning" }, "X-Threlium-Litellm-Req-Seq": { equalTo: "N" } }`
  + позитивный **seed-гейт** сценария (`customMatcher`: `hasContext(thread-root)` + `hasProperty <seed>` —
  напр. `active` или `phase_<scenario>_e2e`; сид ставит setup-стаб ДО контура и НЕ мутирует → это не RUW) +
  body-маркер сценария.
- **Ответ:** статический per-hop JSON (нужный `tool_call`/`finish_reason`). Per-phase seeded-ответы больше НЕ нужны.
- **`recordState`:** НИЧЕГО не продвигает. Только append-only `list.addLast { cs }` (call-site-учёт, §3.6.1) и —
  если тест читает контент-флаг — additive-presence `addLast` в `<kebab>-<thread-root>` (§3.6.7), читается `list_size`.
- **Запрещено:** `hasNotProperty`/`doesNotContain`/`priority` и любой `recordState phase:=N+1`.

**N — это `thread_len`, НЕ 1,2,3.** N = полная глубина IRT-цепочки на момент вызова, т.е. считает и промежуточные
не-reasoning письма (enrich/formal_reason/tasks/enrich_fast/…). Поэтому reasoning-хопы идут с пропусками
(technical_gate: `3,6,13,16`; gate_recovery: `3,6,9,16,23,26`). **Карта seq снимается прогоном:** запустить тест и
прочитать `X-Threlium-Litellm-Req-Seq` каждого reasoning-хопа из журнала `GET /__admin/requests` (или из
INFO-лога движка `e2e_fsm_corr_set` → `thread_len`). Незамапленный хоп → нет стаба → `LLM HTTP 404` на
`reasoning.py` → поток встаёт ровно там → видно следующий seq → добавить стаб → повторить.

**`req_delta` НЕ нужен.** Разные операции в одной стадии уже различает `call-site` (reasoning vs
extract_knowledge_graph vs embeddings — все на одном `thread_len`). А тот же call-site дважды в одной стадии не
бывает: `_decide` делает РОВНО ОДИН reasoning-completion на заход (ниже).

**`_decide` — один completion на стадию (продуктовое следствие, 2026-06-17).** Внутренний `while`-retry убран:
каждый «плохой» исход (`finish_reason=length` / нет `tool_call` в restricted / не тот tool) эмитит в
`enrich_fast` НОВЫМ письмом, причём текст ошибки едет **`<history>`-частью** — канал, который собирает
`enrich_context.collect_unified_delta_msgs` (он берёт только письма с `<history>`; `<system>`-часть на
reasoning-origin письме НЕ собирается → подсказка потерялась бы). `enrich_fast` вплетает её и возвращает в
reasoning (или эскалирует в полный enrich при переполнении токен-бюджета → `summarize_context`). Поэтому КАЖДЫЙ
reasoning-вызов = отдельное письмо со своим `thread_len`; терминирование цикла — по **хоп-бюджету** (декремент
на каждом FSM-переходе), без частных капов. Это и делает `(call-site + req_seq)` полным и однозначным.

**ПЕРЕМЕННОЕ число хопов — больше НЕ проблема.** Старый фазовый счётчик «упирался» в незасиженную фазу → крах →
застрявший `unread` → таймаут teardown-drain; лечили absorbing-`207` + авто-паддинг до 8 фаз. С `thread_len`
счётчик не «кончается» — у каждого хопа свой номер. Лишние хопы реального агентного клиента: добавить стаб на
ожидаемый seq либо оставить generic `200-207` как fallback. absorbing-`207`/авто-паддинг — **обсолетны**.

**Retry-дубликаты стабов — обсолетны.** Раньше пара стабов (один gated `hasNotProperty phase_Y`, дубль без
advance) ловила ре-доставленный хоп идемпотентно. Теперь ретрай переиспользует тот же seq → тот же стаб → тот же
ответ. Дубли удаляются (пример: gate_recovery `106`/`107`).

**ХРУПКОСТЬ (принимается, owner-rule).** `req_seq` = позиция в пайплайне, поэтому изменение ЧИСЛА LLM-вызовов/
писем сдвигает номера и ломает захардкоженные seq. Это **детерминированно** (не флак) и желательно: скрытый
дрейф числа вызовов ДОЛЖЕН заставить пере-проверить тест (в духе «тесты только усиливаются», §3.6.2). При
падении читаем фактический `req_seq` из журнала/INFO-лога и правим.

> **⭐⭐ КРИТИЧНО (2026-06-18): `req_seq=thread_len` ⇒ ВСЕ token-bearing LLM-стабы обязаны быть детерминированы
> по ЧИСЛУ ТОКЕНОВ ответа и изолированы от кросс-тест коллизий. См. §3.6.9 — это не reasoning-стабы, а
> `generate_rag_answer`/`summarize_thread_context`/`extract_query_keywords`, которые под `-nN` молча сдвигают
> `req_seq` через overflow→summarize петлю.**

**AND по позитивным предикатам подтверждён в исходниках (vendored).** Несколько предикатов в одном flat-объекте
`parameters` агрегируются через AND: `StateRequestMatcher.getMatchers` собирает ВСЕ предикаты → `calculateMatch`
→ `MatchResult.aggregate` → `WeightedAggregateMatchResult.isExactMatch()` =
`matchResults.stream().allMatch(::isExactMatch)` (точный матч ТОЛЬКО если ВСЕ под-предикаты точны). `property`/
`hasProperty` матчатся через `StringValuePattern.match` (позитивное сравнение). Header-матчи (`equalTo`)
агрегируются тем же AND на уровне request-матчинга WireMock. Явные `and`/`or`/`not` — массивами
(`StateRequestMatcher` ветки `And`/`Or`/`Not`). Источники:
`vendor/wiremock/wiremock-state-extension/.../requestmatcher/StateRequestMatcher.java` (:66, :157-177, :214-227),
`vendor/wiremock/.../matching/WeightedAggregateMatchResult.java` (:41-46). Это обосновывает, что
seed-гейт + header-матч (`req_seq`) + body-маркер складываются через AND без негативных предикатов.

**Конкурентность.** Reasoning-хопы последовательны на handler-треде (один воркер на тред, §1); seed/thread-root
изолируют тесты друг от друга; контент-флаги — append-only single-writer (§3.6.7). `req_seq` стабилен к ретраю,
поэтому дубликат под `-n12` не рассинхронизирует выбор стаба (главная причина перехода с фазового RUW).

**LEGACY (для понимания старых стабов).** Фазовый счётчик (`property phase==N` + `recordState phase:=N+1`,
container-proven 2026-06-14) был промежуточным детег-подходом. Он РАБОТАЛ для последовательных хопов, но
(а) read-modify-write → не идемпотентен к litellm-ретраю под `-n12` (дубликат продвигал фазу → рассинхрон →
`404`-сторм), (б) «кончался» на переменном числе хопов. Заменён на `(call-site + req_seq)`. Generic `200-207`
пока сохраняют фазовый машинерий как fallback для незамапленных хопов; целевое — и их на `req_seq`.

### 3.6.9. Детерминизм token-bearing стабов — нонсы и кросс-тест коллизии ⭐⭐⭐

**Проблема (расследовано до дна 2026-06-18, минимальный репро = трио `cli_discovery` +
`cli_route_collision` + `formal_reason_gate_recovery_matrix` под `-n3`).** `req_seq = thread_len` (§3.6.8)
сдвигается, если меняется ЧИСЛО LLM-вызовов в треде. Под токен-переполнением enrich запускает петлю
`overflow_to_summarize → summarize_context → summarize_memory → enrich → reasoning` (`states/enrich.py`,
`states/summarize_context.py`); **число РАУНДОВ суммаризации razor-чувствительно к суммарному числу токенов
контекста** (`context_token_count.py`: `total = mandatory + history` vs `effective_budget`). Тест
`gate_recovery_matrix` спроектирован работать РОВНО на границе round-count (2↔3 раунда). Поэтому **любая
вариация ±10-20 токенов в ответе НЕ-reasoning стаба флипает round-count → +3 к `thread_len` → следующий
reasoning-хоп стреляет на seq=26 (finalize) вместо seq=23 (tasks_upsert) → `tasks_upsert` пропущен → ledger
`{open,no-done}` → finalize fail-closed-блок → петля → seq=29 ∉ карты → `404` → застрявший `unread` →
reply-timeout.** Это НЕ форк треда (форки невозможны: один handler = одно письмо, `fsm.py:_run_stage`) и НЕ
RAG-retrieval-объём (общий стор архитектурно отдаёт чанки всех тредов, но инжектится в enrich только
`llm_text` из `aquery`, НЕ сырые чанки → `mandatory` ≈ const). Три **независимых дефекта стабов** дают эту
вариацию:

**ДЕФЕКТ 1 — нонс `{{randomValue}}` в ответе (token-варьирующий).** ~50 стабов имели
`{{randomValue length=16 type='ALPHANUMERIC'}}` в JSON-ответе (`generate_rag_answer`,
`summarize_thread_context`, `extract_query_keywords`) — чтобы «выглядеть уникально»/обойти LLM-кэш. **Доказано
токенайзером (`TiktokenTokenizer('gpt-4o-mini')`, 100 проб): случайный 16-симв alphanumeric = 8-14 токенов
разброс; полный RAG-ответ 26-33 токена (спред 7).** На 5-7 RAG-вызовов + 2-3 summary за тред = кумулятивно
±20-40 токенов → флип round-count. **Фикс: заменить `{{randomValue}}` на ФИКСИРОВАННУЮ строку** (нонс в ОТВЕТЕ
не нужен для кэш-обхода — LLM-кэш ключуется по ЗАПРОСУ; на cache-miss стаб всё равно срабатывает, обёртка
покрыта). Длина строки = const ⇒ токены const.

**ДЕФЕКТ 2 — кросс-тест коллизия `generate_rag_answer` (разная длина ответа).** Per-dir `082_chat_enrich_rag_response`
стабы матчат только `call-site=generate_rag_answer` + body(`"tools"`) **БЕЗ маркера тела**, ответы РАЗНОЙ
длины (89 vs 101 симв). Под `-n3` все 3 теста загружают свои `082` в общий WireMock; reverse-insertion-гонка →
стаб ДРУГОГО теста отвечает на запрос gate_matrix → ответ чужой длины → токен-каскад. **Маркер-изоляция для RAG
НЕВОЗМОЖНА:** RAG-retrieval архитектурно тянет чанки ВСЕХ тредов из общего стора (owner-rule — менять нельзя),
поэтому RAG-ЗАПРОС содержит документы чужих тестов с ИХ маркерами (`E2E-CLI-ROUTE-COLLISION-BODY` протекает в
запрос gate_matrix; доказано: при `-n0` в запросе только свой маркер, при `-n3` — чужой). **Фикс: УНИФИЦИРОВАТЬ
все `generate_rag_answer` стабы в ОДНУ идентичную строку фиксированной токен-длины** (= длина токенов
«рабочей точки» самого чувствительного теста, чтобы не сдвинуть его round-count). Коллизия становится
безвредной (любой стаб → тот же ответ). Контент RAG-ответа ни один тест не ассертит → унификация безопасна.

**ДЕФЕКТ 3 — кросс-тест коллизия `summarize_thread_context` (разная длина саммари).** Та же дыра: per-dir
`075_chat_summarize_context` без изоляции, длины саммари 55-194 симв. gate_matrix получал чужой 110-симв
саммари вместо своего 194-симв → история сжималась на ~84 симв (~20 токенов) меньше → лишний раунд → seq-сдвиг.
**ОТЛИЧИЕ от RAG:** (а) summarize суммаризирует СВОЮ историю (не retrieval) → маркер бы сработал, НО ненадёжен —
поздний раунд сжимает уже-саммари, маркер тела отсутствует (проба: req seq=20 без маркера, seq=13 с маркером);
(б) контент саммари **load-bearing** — `gate_recovery` ассертит сохранение error-наблюдений `PARSE ERROR`/
`QUERY ERROR`/`turtle_syntax SHACL` через суммаризацию (`formal_reason_assertions.py`), унифицировать в generic
НЕЛЬЗЯ. **Фикс: пометить body-маркером `075` тех тестов, что НЕ суммаризируют** (короткие cli-тесты с seq 3,6,9
не переполняются → их `075` = мёртвый груз, только поачит) — они перестают матчить запрос суммаризирующего
теста, и тот получает СВОЙ `075` (как при `-n0`, побеждает generic по reverse-insertion). Надёжный дискриминатор
суммаризирующего теста (если нужен на ЕГО стабе) = контент-keyword, присутствующий во ВСЕХ раундах (напр.
`turtle_syntax` для gate_matrix), НЕ маркер тела.

**ОБЩИЙ ПРИНЦИП (норматив).** Два ОТДЕЛЬНЫХ требования к стабам — у них разный охват:

- **(1) Изоляция от кросс-тест коллизии — для ВСЕХ стабов БЕЗ ИСКЛЮЧЕНИЯ.** Любой стаб, который под `-nN`
  может сматчить запрос ЧУЖОГО теста (тот же call-site/url/body-предикат, загружен в общий WireMock), ОБЯЗАН
  иметь дискриминатор, отбирающий ТОЛЬКО свои запросы: body-маркер сценария (надёжный, если присутствует во
  ВСЕХ релевантных запросах), seed-гейт по thread-root (`hasContext`+`hasProperty <scenario-seed>`), либо
  контент-keyword, гарантированно живущий в каждом целевом запросе. Это касается reasoning, egress, cli,
  RAG, summarize, keyword — ЛЮБОГО стаба. «Generic-bootstrap» стабы (200-207, embed, rerank) изолируются
  иначе — они НАМЕРЕННО общие и отвечают одинаково всем (§3.6.6): тогда коллизия безвредна по построению.
  Правило: либо стаб отвечает **строго своему** тесту (дискриминатор), либо **одинаково всем** (generic,
  идемпотентный ответ). Запрещён третий случай — стаб БЕЗ дискриминатора, но с **тест-специфичным** ответом:
  под reverse-insertion-гонкой он поачит чужие запросы (корень дефектов 2/3 ниже).
- **(2) Детерминизм по числу токенов ответа — для token-bearing стабов** (`generate_rag_answer`,
  `summarize_thread_context`, `extract_query_keywords`, любой enrich/summarize-инжектируемый, чей ответ
  попадает в токен-бюджет). Никаких `{{randomValue}}`/`{{now}}`/переменной длины. Для них коллизия (даже
  «безвредная» по логике) ВРЕДНА, если ответы разной длины → токен-каскад → сдвиг `req_seq`; поэтому им нужна
  И изоляция (1), И равная токен-длина (унификация, если контент не ассертится).

Иначе под `-nN` token-bearing стаб молча сдвигает `req_seq` суммаризирующих тестов, а ЛЮБОЙ не-изолированный
стаб может отдать чужой/неверный ответ (сломав логику или ассерт напрямую, не только через токены). Это
РАСШИРЯЕТ §3.6.5 (embed-стаб) и §3.6.8 (хрупкость req_seq): хрупкость касается не только ЧИСЛА
reasoning-вызовов, но и ТОКЕН-РАЗМЕРА любого инжектируемого ответа И корректности изоляции каждого стаба.

**Диагностика (метод, воспроизводимо).** (1) Подтвердить, что флак = пропуск seq: выровнять `decision`-события
по `thread_len` (из `e2e_fsm_corr_set`) в `-n0` PASS vs `-nN` FAIL — увидеть пропущенный seq. (2) Исключить RAG
как причину: залогировать `llm_text` ответа `aquery` в enrich (len+sha) — `mandatory` const ⇒ не RAG. (3) Найти
варьирующий юнит: per-unit брейкдаун `history`-юнитов (`enrich_context.build_unified_email_messages`, лог
From/MID/char-len каждого kept-юнита) в `-n0` vs `-nN` — diff укажет на summary-юнит (или иной). (4)
Подтвердить коллизию: `GET /__admin/requests` на живом WireMock — у какого стаба какой ответ/длина пришёл
целевому треду. (5) Токенайзер на хосте (`lightrag.utils.TiktokenTokenizer`) — измерить разброс кандидата-нонса
прямо, не гадать по флакам.

**Owner-rule:** `req_seq`-чувствительность к этим вариациям = ЦЕЛЬ, а не баг — она ВСКРЫВАЕТ недетерминизм
стабов наружу (детерминированно, не флак-маскировкой). Лечим ИСТОЧНИК (нонс/коллизия), не симптом (404/storm —
их трогать запрещено, §«retry-storm»). Шторм/`404` = корректное поведение, выставляющее баг быстро.

**Isomorph (long-hold).** Прямой HTTP-мост: тест POST-ит тело сам → не нужен bootstrap-транспорт. Изоляция —
`E2E_MID:` thread-root (§2.3). Egress пушит ответ обратно в мост (`/internal/v1/push`); тред-непрерывность — не
голосование, а **невидимый водяной знак** glue-MID в content ответа (клиент возвращает его в истории →
`In-Reply-To` следующего хода). Детали — [BRIDGE_ISOMORPH.md](BRIDGE_ISOMORPH.md).

---

## 4. Каналы: коррелятор + транспорт

После моста все каналы → email (`build_bridge_ingress_email`) с уникальным `X-Threlium-Route`; дальше pipeline
(enrich → reasoning → egress) изолирован **одинаково** — `hasContext` по `X-Threlium-Thread-Root`. Различается
только **как получить коррелятор** и транспортный bootstrap.

| Канал | Коррелятор (thread-root) | Транспорт-bootstrap | Egress-стаб |
| --- | --- | --- | --- |
| **Email** | MID старейшего `tag:route` треда; тест: inner SMTP-инъекции (`e2e_smtp_inject_ingress_route_wire_for_message_id`) | SMTP→GreenMail→IMAP bridge | `sendMessage`/msmtp (не state-matcher) |
| **Matrix** | `RfcMessageIdWire(MatrixNativeId(room_id,event_id))` корневого события | один `/sync` + shared list `matrix_rooms` (`#each`) | `room_send`: state-matcher (nio custom_headers) |
| **Telegram** | MID из `chat_id`/`message_id`/`message_thread_id` | один `getUpdates` + shared list `telegram_updates` | `sendMessage`: bodyPatterns (PTB не шлёт thread-root на wire) |
| **Isomorph** | `E2E_MID:` (§2.3) или content-hash; прод — snowflake | прямой HTTP в мост (long-hold) | egress push в мост; водяной знак в ответе |

**Shared-list каналы (Matrix/Telegram).** Мост делает **один** `/sync` (или `getUpdates`) на весь homeserver,
поэтому ответ должен содержать события **всех** активных тестов. Решение — общий контекст с `list`:
тест в setup `register_room`/`register_update` (`addLast` своей записи), bootstrap-стаб собирает ответ `#each`
по list; в `finally` `unregister_*` (`deleteWhere` по `room_id`/`update_id` — **только своё**). Один
bootstrap-стаб **без** `state-matcher`/`listSizeMoreThan` (пустой list → пустой ответ, не unmatched).
List-операции из разных xdist-воркеров сериализуются тем же межпроцессным `_wiremock_admin_api_exclusive`
(FileLock `e2e_wiremock_admin_api.lock`), что и Admin GET. Дедуп повторных событий — на мосту (notmuch по MID).

## 5. Параллельная безопасность (`pytest -n N`)

**Цель** — одновременно нагрузить и xdist-воркеры, и несколько notmuch-тредов в SUT (контракт
*serial-per-thread, parallel-across-threads*, [ORCHESTRATION.md](ORCHESTRATION.md)). `pytest.mark.
xdist_group("exclusive")` и любая exclusive-сериализация e2e **запрещены** — при гонках расширяют якоря, а не
отключают параллельность.

**Что параллельно-безопасно:** изолированный коррелятор на тест (§2) + узкие `bodyPatterns`/`X-Threlium-Call-Site`
+ свой каталог стабов/`stub_tag`. Несколько воркеров одновременно бьют в один WM — каждый запрос **обязан**
сматчиться своими стабами; иначе unmatched и любой воркер падает на guard.

**Журнальный guard** (нормативный инвариант целостности стабов): `GET /__admin/requests/unmatched` **глобально
пуст** — проверяется в `pytest_runtest_call` до и после тела каждого теста ([conftest.py](../tests/e2e/conftest.py)).
Фильтр по заголовкам **не** применяют (у unmatched-запроса может не быть `X-Threlium-Route`). Единственный
допустимый FileLock — вокруг самого Admin GET в `wiremock_unmatched_request_entries` (иначе 500 WM при
параллельных опросах); сам хук локами не сериализуют.

**Запрещено из кода сценариев** на общем WM: `wiremock_state_reset_all_contexts`, `reset_request_journal`
(`DELETE /__admin/requests`), глобальный `DELETE /__admin/mappings` — снесут чужие воркеры (bootstrap, State).
Исключение — **один** координированный cold reset на инвокацию pytest (`_e2e_wiremock_journal_reset_once`):
под FileLock лидер **до** параллельных тестов готовит детерминированный индекс ровно один раз — снимает
pipeline → сбрасывает WM (журнал, Store, `reset_non_bootstrap_wiremock_mappings`, `compose_bootstrap/`) и
GreenMail → flush Maildir+notmuch+**весь LightRAG** (redis `FLUSHALL` + `rm lightrag`; в индекс за прогон
попадает каждое письмо, поэтому wipe обязателен) → ставит ОДИН probe-документ из `tests/e2e/fixtures` вместо
запечённого корпуса → поднимает engine (bootstrap эмбедит ровно probe) и ждёт persist `doc_status` → второй
рестарт без wipe (идемпотентность → `[DUPLICATE:filename]`). Это НЕ thrash: рестарты идут под локом, пока
параллельных контуров ещё нет (см. `n4-coldreset-thrash-rootcause`).

**Collision-at-root (центральный урок -n2).** Контент-адресуемые коррелятор/glue-MID **коллизируют** при
идентичном содержимом → notmuch сливает треды → каскад unmatched/зависаний. Под `-n2` лечится: (1) test-уникальные
тела И ответы, либо (2) явный `E2E_MID:` (§2.3). Прод снимает это в корне уникальными snowflake-MID.

**Изоляция журнала — по thread-root, НЕ по `stub_tag` (урок -n2-каскада).** `stub_tag` зашит в JSON каталога
стабов, поэтому **совпадает у тестов, переиспользующих один каталог** (telegram private + duplicate_skip;
summarize overflow + idempotent; task-ledger chain `*_chain_e2e` + параметрический `[task_ledger_chain]`).
`prepare_wiremock_scenario` раньше чистил журнал `remove_wiremock_journal_by_stub_tag` — на общем `-n2`-WM это
стирало matched-записи **параллельного** теста с тем же тегом → его journal-ассерт ловил ложный «0»/timeout, и
по глобальному guard каскадом падали все последующие. Чистка переведена на **свой thread-root**
(`remove_wiremock_journal_by_thread_root`: `POST /__admin/requests/remove` по заголовку `X-Threlium-Thread-Root`)
— тест трогает только свой тред, никогда чужой. По той же причине per-test journal-**поиск** должен скоупиться
по thread-root/уникальному ключу (chat_id), а не по одному `stub_tag` (иначе over-count соседа). Идеал —
вообще не зависеть от журнала, считать на лету в state (§3.6; рецепт миграции + container-proof
независимости state от журнала — **§3.6.7**). `stub_tag` остаётся только для cleanup стабов и
диагностики, не для изоляции (см. §2: изоляция = коррелятор-заголовок).

**Журнал кумулятивен за сессию.** Журнал WireMock не чистится per-test (только cold reset в начале сессии +
свой thread-root); полный `-n2` суммарно даёт тысячи записей, упираясь в ring-buffer
`--max-request-journal-entries` (§9). Но вытесняются **старейшие** (ранних тестов), а тест ищет matched по
`matchingStub=<uuid>` свои **свежие** записи — поэтому вытеснение само по себе не роняет ассерты; поднимать
лимит бесполезно (проверено: 2500 vs 20000 — тот же набор падений) и лишь добавляет память/GC WM. Идеал —
вообще не зависеть от журнала, считать на лету в state (§3.6; **§3.6.7** — почему это всегда возможно:
state-контексты переживают полную очистку журнала, container-proven).

**Таймаут под `-n2` = всегда баг, НЕ «ёмкость среды».** ⚠ Ранняя редакция этого раздела ошибочно списывала
массовые `-n2`-таймауты тяжёлых контуров (reasoning/summarize/telegram/matrix) на «ёмкость среды / нагрузку».
Это было НЕВЕРНО — за ними стояли два конкретных бага, теперь устранённых: (1) **cold-reset thrash** — на
частичном сбое bootstrap-WAIT cold reset не ставил marker и **деструктивно пере-рестартовал общий движок на
КАЖДОМ тесте** → `rag_shutdown_cancelled_tasks` рубил in-flight RAG всех параллельных контуров (thrash-guard:
marker ставится независимо от исхода wait); (2) **cozo `has_node`/`has_edge` unbound-symbol halt** — фильтр в
голове Datalog-правила всегда кидал `eval::unbound_symb_in_head`, pycozo маскировал ошибку → LightRAG halt'ил
ОБЩИЙ pipeline → документы соседних тестов застревали PENDING → их таймауты. После фиксов полный `-n2` = 73/0,
wall 602s→289s. **Доктрина (§1): таймаут — это контур, который где-то завис; ищи стопор (py-spy/прямая проба в
изоляции), не списывай на нагрузку и не повышай таймаут.** См. `timeouts-mean-hidden-bug`,
`n4-coldreset-thrash-rootcause`.

**Никаких skip/serial/xdist_group.** Тест НЕ должен выключаться под xdist или сериализоваться — изоляция по
коррелятору (§2) делает это ненужным; глобальные мутации (env+рестарт общего сервиса) чинятся в корне
(per-message конфиг через тело письма, см. `E2E_MID:`-паттерн §2.3), а не skip-ом. `pytest.skip(...
PYTEST_XDIST_WORKER)` и `@pytest.mark.xdist_group` **запрещены**; оставшиеся — миграционный долг к устранению,
не образец. Пример правильного хода — upstream-timeout→504 (§7.4): per-request `E2E_REQUEST_TIMEOUT_SEC` через
тело, без глобального рестарта.

---

## 6. Харнесс

### 6.1. Compose-стек

[`tests/e2e/compose/docker-compose.yml`](../tests/e2e/compose/docker-compose.yml): `sut`, `greenmail`, `wiremock`.

- **`sut`** — privileged + cgroup host + mount `/sys/fs/cgroup` (нужно для `loginctl enable-linger`,
  `systemctl --user`, `.path`-юнитов с inotify). Baked-образ `threlium/e2e-sut:baked`. Cockpit HTTPS :9090,
  Caddy HTTP :8080.
- **`greenmail`** (`standalone:latest`) — SMTP 3025 / IMAP 3143 (pytest с хоста) / IMAPS 3993 (мост в SUT);
  TLS PKCS#12 `greenmail.p12` (SAN `localhost`/`greenmail`/`127.0.0.1`). Динамический host-port (`"3025"`),
  pytest находит через `_mapped_port`.
- **`wiremock`** (`wiremock:latest`, host 9080→8080, `--global-response-templating`) — **единственный** HTTP-mock
  для OpenAI-совместимых вызовов (`/chat/completions`, `/embeddings` — без `/v1/`), Matrix (`/_matrix/…`),
  Telegram Bot API. State-extension JAR + classpath.

### 6.2. Baked-образ SUT

**Bake** — на bootstrap-образе (`geerlingguy/docker-ubuntu2404-ansible`) прогоняется тот же `site.yml`, что в
проде → `docker commit` в `threlium/e2e-sut:baked`. В образе — развёрнутая система, **источник правды —
`site.yml`, отдельного Dockerfile нет**. `ensure_e2e_sut_image_exists`: reuse по `docker image inspect`;
форс — `THRELIUM_E2E_REBUILD_BAKED_IMAGE=1` или `pytest -n0 tests/e2e/wipe_bake.py` (под локом
`/tmp/threlium_e2e_bake_image.lock`). Пересобирать при: правках `site.yml`/ролей/apt/pip/bootstrap-образа.
Правки только Python-кода Threlium/тестов/докум. — **не** повод.

### 6.3. Shared compose + filelock

Дефолт — **однопоточный** `pytest tests/e2e` (лидер = единственный участник). Параллельный контракт — **явно**
`-n N`; `addopts = -n N` в `pyproject.toml` **не** ставить. Все xdist-воркеры делят **один** compose-проект
`threlium_e2e_shared_{hex}`: первый под `FileLock` поднимает стек и пишет `ready.flag` + `runtime.json`
(`e2e_compose_coord_paths()`), остальные читают `project_name` / `discover_runtime`. «Мёртвый» координатор
(файлы есть, стек остановлен) — лидер проверяет running-контейнеры через Docker API и сбрасывает флаги.
`pytest_sessionfinish` **не** делает `compose down` (reuse; opt-in `THRELIUM_E2E_COMPOSE_DOWN=1`).

### 6.4. Фикстуры и toolkit

| Фикстура | Скоуп | Роль |
| --- | --- | --- |
| `compose_stack` | session | Attach-only к healthy стеку; session cold reset (`_e2e_wiremock_journal_reset_once`), `runtime.json`. |
| `e2e_runtime` | function (autouse) | Per-test: **read-only** discover общего стека (`discover_runtime`). Без рестартов/чисток/reindex — изоляция держится на динамических корреляторах (§3.6.1), а детерминированный bootstrap-индекс готовит session cold reset один раз до параллельных тестов. |

Toolkit ([`tests/e2e/toolkit/`](../tests/e2e/toolkit/)) — пакет harness: runtime/compose-обвязка, SUT image
strategy, polling через `tenacity` (`poll_until` fixed / `poll_until_backoff` exp, progress каждые 15c),
GreenMail/IMAP/notmuch waiters, WireMock-журнал, ansible, диагностика. Контракт-константы — `E2E_BAKED_SUT_IMAGE`,
`E2E_THRELIUM_USER`, `E2E_WIREMOCK_CONTAINER_PORT`, `E2E_REPLY_SUBJECT`/`E2E_REPLY_BODY_SNIPPET`, …

**Стабы — только статический закоммиченный JSON (нормативно).** Маппинги живут в git как
`wiremock_stubs/<тест>/*.json`; `compose_bootstrap/` — инфраструктурный (`recordState` setup/phase_reset,
matrix/telegram register, embeddings readiness, **state-readout probes** §3.6; тег
`THRELIUM_WIREMOCK_COMPOSE_BOOTSTRAP_STUB_TAG` переживает cold reset). **Запрещена любая динамическая
генерация/модификация стабов** — ни сборка/патч тел из pytest (временные каталоги, `replace`/Jinja2 по JSON,
Python-сборка `mapping`), ни инъекция `serveEventListeners`/полей в загрузчике на лету. «Динамика» делается
**внутри статического стаба**: `recordState`-листенер + `state`-helper (state-extension) считают/пишут
состояние во время обслуживания — это и есть state-asserts (§3.6), которые и позволяют не генерировать стабы
динамически. **Разрешено** в рантайме только: `wiremock_state_*` (сид/reset/чтение контекста),
`upsert_wiremock_mapping_directory` (грузит JSON как есть; стабильный `id` =
`wiremock_stub_id_for_e2e_stub_relpath`, в metadata — `stub_tag` для cleanup), `{{randomValue …}}` в ответах.
`stub_tag` **не** выбирает стаб на стороне WM и **не** основа изоляции (изоляция = thread-root, §2) — он только
для cleanup стабов.

### 6.5. Деплой в SUT + режимы прогона

Сценарные тесты **не** вызывают `ansible-playbook`. `site.yml` (полный или `--tags repo` для быстрого цикла
кода/конфигов; `--tags refresh` — сброс mail-state + рестарт user-units) — отдельный шаг до сценариев. Только
код `scripts/threlium`+`prompts/` на живом SUT без плейбука — [FSTS_SYNC.md](FSTS_SYNC.md).

| Команда | Что |
| --- | --- |
| `pytest tests/e2e` | Однопоточный прогон (дефолт), attach к baked-стеку. |
| `pytest tests/e2e -n 8` | Параллельный стресс — проверка thread-parallel контракта. |
| `pytest -n0 tests/e2e/wipe_bake.py` | Полный bake образа + сброс координаторов + `compose down`/`up`. |
| `pytest -n0 tests/e2e/wipe_sync.py` | Только harness (`--tags refresh`) на уже поднятом SUT. |

`wipe_*.py` **не** в дефолтной коллекции (имена вне `test_*.py`; не расширять `python_files` до `*.py`).

---

## 7. Паттерны: тестирование long-hold моста (isomorph)

Мост `isomorph` держит HTTP-соединение (long-hold) до egress-push. Эти паттерны переиспользуемы для любого
поведения долгоживущего соединения; все изолированы своим `E2E_MID:` thread-root (§2.3), стабы — переиспользуют
L0-цепочку json-вариантов (FSM-путь тот же; surface меняет лишь кодирование запроса/ответа моста).

### 7.1. Прямой SSE wire-shape (+ keep-alive)

Тест — прямой `stream:true` клиент (`bridge_post_sse` → `curl -N` изнутри SUT), читает **сырой** SSE-поток и
проверяет строгую wire-схему вендора **побайтово** (независимо от толерантности реального Cline):
- **Anthropic**: `message_start → content_block_start → content_block_delta → content_block_stop →
  message_delta → message_stop`; текст ответа — в дельтах.
- **OpenAI**: role в первом чанке, content-чанк, usage-чанк с пустым `choices`, терминатор `[DONE]`, каждый
  кадр `object == chat.completion.chunk`.

`parse_sse_events` разбирает кадры в `(event|None, data)`. **Keep-alive покрывается тем же тестом**: при
`keepalive_sec=20 < оборот FSM (~30c)` под `-n2` в потоке естественно появляется `event: ping` (Anthropic) /
`: keep-alive` (OpenAI) **до** ответа — поэтому `ping` исключают из проверки **порядка** (он валиден где угодно),
но требуют наличие каркасных событий.

### 7.2. Client-disconnect mid-hold

`bridge_post_sse(timeout=4)` обрывает клиента ПОСРЕДИ удержания (`exec_run` не бросает на `rc!=0` → возвращает
частичный поток). Проверка: мост чистит pending своего коннекта (generator `finally` → `forget`, поздний push =
no-op) и **переживает** (health отвечает), а in-flight ход FSM **не** обрывается (независим от коннекта) —
доходит до glue (ARCHIVE-FIRST). **Свой fresh marker обязателен** (иначе stale phase-latch §3.3 → finalize-loop →
teardown зависает).

### 7.3. FSM-error → error-envelope

`error_message` в push → мост отдаёт held-запросу вендорный error-envelope (HTTP 500 `{"error":{…}}`).
Тест: один `sut_exec` фоном держит `stream:false` запрос, через ~5c инъектит push в `/internal/v1/push`
(`bridge_post_json_with_pushed_error`, секрет `e2e-isomorph-push-secret`, `ingress_mid = e2e_explicit_root_corr`
— inner-форма ровно как мост) — push **опережает** FSM (~30c), мост резолвит held ошибкой. Стабы засижены → реальный
ход доходит чисто в фоне (late push = no-op), teardown idle без зависа.

### 7.4. Upstream-timeout → 504 (serial-only)

Мост отдаёт 504, если push не пришёл за `request_timeout_sec` (дефолт 180c). Чтобы не ждать — serial-only
фикстура (skip под xdist, §5) понижает таймаут до 8c (env `THRELIUM_BRIDGES__ISOMORPH__REQUEST_TIMEOUT_SEC` в
`/home/threlium/threlium/agent/env/threlium.env` + рестарт моста) и **восстанавливает в `finally`**. Запрос
держится → мост снимает pending → 504. `curl --max-time 40 > 8` → ловим именно мостовой 504, не клиентский обрыв.

---

## 8. Жизненный цикл State

```
┌─ pytest session start (лидер под FileLock, один раз) ──────────┐
│  cold reset: stop pipeline → flush Maildir/GreenMail            │
│  → reset WM journal + Store + non-bootstrap mappings           │
│  → bootstrap stubs → start engine → idle → journal reset       │
├─ per-test setup (фикстура сценария) ───────────────────────────┤
│  wait idle → wait bridge health → clean_*_test_threads(marker)  │
│  → upsert stubs (свой каталог/stub_tag) → seed context          │
│  → [matrix/tg] register_room / register_update                  │
├─ test body ────────────────────────────────────────────────────┤
│  SUT: bridge → ingress → enrich → reasoning → egress            │
│  каждый LiteLLM-запрос несёт X-Threlium-Thread-Root + Call-Site │
│  state-matcher: composite hasContext + phase; recordState        │
│  guard: GET /requests/unmatched пуст (до и после тела)          │
├─ test teardown (finally) ──────────────────────────────────────┤
│  [matrix/tg] unregister (deleteWhere — только своё)             │
│  контекст route НЕ удалять (поздний трафик SUT)                 │
│  matched-журнал НЕ чистить (остаётся для отладки)               │
├─ pytest_sessionfinish (один раз) ──────────────────────────────┤
│  wait idle + assert zero unmatched → wiremock_state_reset_all   │
│  при FAIL: укороченный drain (FAIL_DRAIN_SEC, 30c)              │
└────────────────────────────────────────────────────────────────┘
```

Контекст route в function-teardown **не** удаляют: поздние LiteLLM-запросы SUT (after test body) должны
по-прежнему матчиться. Полный сброс Store — только в `pytest_sessionfinish` (`wiremock_state_reset_all_contexts`)
**после** idle и пустого unmatched. `e2e_clean_sut_messages_for_test(stub_tag, correlation_key)` между тестами
удаляет на SUT только письма прошлых запусков **этого** marker'а, сохраняя тред текущего `correlation_key` для
multi-turn.

---

## 9. Практические gotchas

- **`bodyPatterns[].matches` — full-match** (как `String.matches()`): regex должен покрыть **всё** тело
  (`"(?s).*….*"`). Для подстрок — `contains`/`matchesJsonPath`.
- **Handlebars `{` перед блоком** — нужен пробел: `"join":{ {{#each …}}` (иначе `{{{` = triple-stache → exception);
  закрытие `{{/each}} }`.
- **LLM-кэш lightrag-hku** на долгоживущем SUT: повторный `aquery` с тем же текстом/keywords может **не** вызвать
  HTTP backend (`cache_type=keywords/query`) → ожидаемой фазы нет в журнале. Варьировать вход хэша — уникальный
  суффикс в seed-ответе/keywords-JSON/первом сообщении; для chat/embeddings — State-контекст.
- **WireMock OSS metadata** в шаблонах ответа не работает (`{{stub.metadata.…}}` → пусто); вариативность —
  `{{randomValue}}` в ответах, не второй слой шаблонизации до `upsert`.
- **307-цепочка для «долгого LLM»** (без удержания сокета): стаб несколько раз отвечает 307 `Location` на тот же
  URL (httpx следует редиректам внутри одного `send()`, POST→POST для 307); переключение «тест отпустил» — второй
  стаб по `hasProperty`/POST-триггеру. Лимиты: httpx `DEFAULT_MAX_REDIRECTS=20`, reasoning `timeout ≈120c` на всю
  цепочку, `max_retries=0` не отключает следование редиректам. **Требует `follow_redirects=True`** у HTTP-клиента:
  свой `openai_compatible_client.py` (замена litellm) шёл с httpx-дефолтом `follow_redirects=False` → 307 не
  следовался (не 4xx/5xx, не ретраябелен), reasoning падал на парсинге → весь 307-gate ломался. Пример —
  `test_live_telegram_wiremock_private_tail_307_second_message`.
- **LightRAG-стор: KV/doc_status — Redis, vector — LanceDB, graph — CozoDB (НЕ файловый JSON).**
  `doc_status`/`full_docs`/`text_chunks`/`llm_response_cache` — ключи Redis; векторы — таблицы LanceDB в
  `$THRELIUM_HOME/lightrag/`; граф — CozoDB. Удаление `kv_store_doc_status.json` — **no-op** (файла нет):
  движок видит probe как `Duplicate document` и **пропускает embedding** → bootstrap-тест ловит «нет
  e2e-bootstrap embeddings». Для форс-переиндексации bootstrap нужен `redis-cli flushall` + снос
  `$THRELIUM_HOME/lightrag/` (LanceDB-таблицы + Cozo); doc_status читать из Redis (`redis-cli get
  doc_status:*`), не из файла. Reindex делает flushall + рестарт **общего** engine → модуль
  `test_knowledge_bootstrap_live_e2e` serial-only под xdist (§5).
- **Notmuch-дедуп при повторном `/sync`** — штатно (`duplicate Message-ID, skip`).
- **Sessionfinish после FAIL** — это **не** «зависание» runner: guard всё равно ждёт idle + пустой unmatched
  (укороченный `FAIL_DRAIN_SEC=30c`). Параллельные smoke на том же compose с runner не запускать.
- **`recordState` `context` рендерится шаблоном, и ПУСТОЙ контекст → исключение, а не no-op.**
  [`RecordStateEventListener`](../vendor/wiremock/wiremock-state-extension/.../RecordStateEventListener.java) делает
  `renderTemplate(model, rawContext)` и при `isBlank` кидает `"context cannot be blank"` → стаб обслужился (0
  unmatched), но **state не записан**. Поэтому body-content-flag, отрендерившийся в пусто, **молча теряет запись**.
- **`regexExtract` — две формы (vendored `RegexExtractHelper`):** инлайн `{{regexExtract request.body 'pat'}}` (1
  позиционный арг) возвращает **весь матч** (group 0) первого `find()`; варнейм-форма `{{regexExtract … 'pat' 'm'}}`
  кладёт **capture-группы 1..N** в список `m` **0-индексированно** (`group(1)` → `m.0`, НЕ `m.1`!) и сам возвращает
  пусто. Типичные грабли: `{{m.1}}` → out-of-bounds → blank → см. предыдущий пункт. Для извлечения body-коррелятора
  проще инлайн: `{{regexExtract request.body '<[A-Za-z0-9]{40,}@localhost>' default='_nocorr'}}` (длинный base62
  thread-root, дефисные Message-ID не матчатся; `default` спасает от blank на чужом теле).
- **LightRAG `ainsert` УЖЕ параллелит внутри — снаружи не дублировать.** `ainsert` → `apipeline_enqueue_documents` +
  `apipeline_process_enqueue_documents`, пайплайн поднимает `asyncio.Semaphore(max_parallel_insert)` + N воркер-циклов
  ([`pipeline.py:1244,1268`](../.venv/.../lightrag/pipeline.py)). Конкурентные `ainsert` из разных задач дерутся за
  **общий** pipeline + `pipeline_status_lock` и повторно гоняют общую очередь. Прод-параллельность индексации — это
  `max_parallel_insert`/`*_max_async`, **не** внешний пул/много drain-цепочек.
- **Drain singleton — НЕ узкое место, а защита от proliferation.** [`schedule_on_loop`](../ansible/.../lightrag/_drain.py)
  гард = один коллектор/одна sweep-цепочка. Снятие гарда → каждый `schedule_index_pending` спавнит цепочку + self-
  reschedule, и **без claim-on-collect** (тег ставится только в конце `_ainsert_batch`) цепочки повторно собирают тот
  же незатегированный backlog → лавина избыточных ainsert'ов (наблюдалось **6341 `ainsert_complete` на ~200 доков**,
  пачки по 8 с `elapsed≈65c`, разрешающиеся разом) → единый RAG event-loop засатурирован → `aquery` enrich'а голодает
  → contour-таймаут. Симптом «loop голодает» — следствие proliferation, не причина.
- **`_ainsert_with_correlation` берёт thread-root из `file_paths[0]` — на ВЕСЬ батч.** В смешанном батче (письма
  разных thread-root) индекс-вызовы доков 2..N уйдут под коррелятор первого. Корректно только при batch_size=1
  (drain успевает, de-facto 1). Для робастности per-message корреляции индексации в e2e — либо `_effective_batch_size→1`,
  либо per-doc-цикл, либо (выбранное) **body-липкий-флаг + call-site** вместо thread-root для индекс-стабов
  (коррелятор в теле, повторён в каждом чанке — `e2e_dense_threlium_ctx_body`). Прод не затронут: там корреляции нет
  (`_ainsert_plain`), смешанные батчи штатны.
- **Vector store = LanceDB (vector) + CozoDB (graph), НЕ faiss/Milvus/Qdrant.** Требование к стору —
  **concurrent-write-safe** при `max_parallel_insert>1` (иначе рестарт общего движка каскадит на всех `-n4`).
  LanceDB даёт **MVCC** (lock-free конкурентные чтения+записи, Lance-формат) и **нативный async API**
  (`connect_async`, не блокирует event-loop); регистрация — `_construction._register_lancedb_storage`
  (`lancedb_impl.py`), без патча вендора. Cozo — MVCC graph. **Исторический контекст (отвергнутые сторы, не
  citing as current):** *faiss* — гонка на `faiss_index_*.tmp` → **SIGABRT** на параллельной записи; *Milvus
  Lite* — синхронный gRPC морозил event-loop + хардкод `id VARCHAR(64)` отвергал base62-MID (>64) →
  pipeline halt; *Qdrant-local* — embedded Rust-ядро не concurrent-write-safe → SIGABRT (lightrag сам пишет
  «use server Qdrant»). Урок, перенесённый в LanceDB: chunk/doc-`id` — короткий dedup-ключ
  (`_drain._lightrag_doc_id` = `th-`+sha1), notmuch-трекинг отдельно через `tag_ids`; `initialize_pipeline_
  status()` после `initialize_storages()` обязателен (иначе shared_storage `get_data_init_lock` no-op).

---

## 10. Переменные окружения (ключевые)

Ни одна не обязательна для дефолта. Полный список — в коде conftest/toolkit; критичные:

| Переменная | Дефолт | Назначение |
| --- | --- | --- |
| `THRELIUM_E2E_REBUILD_BAKED_IMAGE` | unset | `1` → форс-bake лидером `compose_stack`. |
| `THRELIUM_E2E_SUT_IMAGE` | `threlium/e2e-sut:baked` | Образ `sut` (не-дефолт → off auto-bake). |
| `THRELIUM_E2E_LITELLM_ROUTE_CORRELATION` | e2e: on | Merge HTTP-заголовков корреляции для WM `hasContext`. |
| `THRELIUM_E2E_POLL_SHORT` | `30` | **Постоянный** таймаут poll'ов — не повышать ради медленного контура (чинят стабы/вход/продукт). |
| `THRELIUM_E2E_SESSIONFINISH_DRAIN_SEC` | `120` | Ожидание idle + пустой unmatched перед сбросом Store. |
| `THRELIUM_E2E_COMPOSE_DOWN` | unset | `1` → явный `compose down` после сессии. |
| `THRELIUM_E2E_ANSIBLE_TAGS` / `_SKIP_TAGS` | unset | `--tags`/`--skip-tags` для `ansible-playbook`. |

Команды:
```bash
.venv/bin/pip install -e ".[e2e,dev]"
pytest tests/e2e -vv                                   # дефолт (один процесс)
pytest tests/e2e -n 8 -vv                              # параллельный контракт
pytest -n0 tests/e2e/wipe_bake.py -vv -s && pytest tests/e2e   # полная подготовка
THRELIUM_E2E_COMPOSE_DOWN=1 pytest tests/e2e           # с явным down
```

---

## 11. Связь документов

| Документ | Роль |
| --- | --- |
| [INDEX.md](INDEX.md) | Master-контракт: storage (union root `stages/`), fdm `insert && dispatch`, `nm_settle()`, error handling, LightRAG-воркер. e2e-инварианты выводятся отсюда. |
| [ARCHITECTURE.md §1.3](ARCHITECTURE.md#13-политика-тестирования) | Политика: e2e — единственный quality gate. |
| [ORCHESTRATION.md](ORCHESTRATION.md) | serial-per-thread / parallel-across-threads — контракт, который проверяет `-n N`. |
| [PLAYBOOK.md §2.1](PLAYBOOK.md) | Классы операций (A/B), ограничения, тег `refresh` как тестовая надстройка. |
| [MESSAGES.md](MESSAGES.md) | Канонизация `Message-ID` на границах — основа уникальных коррелятов. |
| [THREAD_MODEL.md](THREAD_MODEL.md) / [BRIDGE_ISOMORPH.md](BRIDGE_ISOMORPH.md) | Производственная тред-идентичность мостов (snowflake-MID, водяной знак glue) — прод-аналог §2.3. |
| [FSTS_SYNC.md](FSTS_SYNC.md) | Синхронизация только кода `scripts/`+`prompts/` на живой SUT без плейбука. |
