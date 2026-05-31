# Formal_reason gate и relay `<system>` (machine payload)

> **Граница документов.** Общий контракт `<history>` / `<system>`, CID, origin, дедуп,
> сбор `enrich_fast`, матрица стадий и презентация `<conversation_delta>` — только в
> [`CONTEXT_CONTRACT.md`](CONTEXT_CONTRACT.md). **Здесь** — исключительно цикл
> `formal_reason`, machine payload `FormalReasonResultPayload`, проброс `@system` в этом
> цикле и FSM gate на входе `reasoning`.
>
> **См. также:** [`FSM.md` §5.2](FSM.md#52-контракт-тела-enrich--reasoning),
> [`TYPES.md`](TYPES.md). Код: `threlium/formal_reason_gate.py`,
> `threlium/states/formal_reason.py`, `threlium/states/enrich_fast.py`.

---

## 1. Два канала в цикле formal_reason

Для этого tool-callee действуют общие правила [`CONTEXT_CONTRACT.md` §1–3](CONTEXT_CONTRACT.md);
ниже — **кто что читает именно для gate**.

| Канал | Кто читает | Содержимое |
|-------|------------|------------|
| `<hash@history>` | LLM (`reasoning/user.j2`) | `observation_*.j2`, request-echo |
| `<hash@system>` | `formal_reason_gate` | JSON `FormalReasonResultPayload` |

Gate **не** парсит prose (`QUERY ERROR`, `FSM locked` в observation). Источник истины FSM —
relayed `<system origin=formal_reason>` на конверте после splice (origin штампует
`enrich_fast`, см. [`CONTEXT_CONTRACT.md` §3](CONTEXT_CONTRACT.md)). Битый JSON →
`RuntimeError` (§4), не silent full toolset.

---

## 2. Поток и место в матрице стадий

```mermaid
sequenceDiagram
  participant R as reasoning
  participant FR as formal_reason
  participant EF as enrich_fast
  participant R2 as reasoning

  R->>FR: system=FormalReasonStagePayload
  FR->>EF: history×2 + system=FormalReasonResultPayload
  EF->>R2: splice: delta history+system
  Note over R2: gate: system only
```

Строка матрицы emit: [`CONTEXT_CONTRACT.md` §3](CONTEXT_CONTRACT.md) (`formal_reason` →
`enrich_fast`: echo + observation + `<system>`). Сквозной пример CID без gate-деталей —
тот же документ §8 (краткая отсылка).

**Специфика relay `<system>` (не повторять общий §5 CONTEXT_CONTRACT):**

- На письме `enrich_fast → reasoning` может быть **несколько** `<system>` (разные стадии в
  дельте); gate смотрит части с `part_origin_label == formal_reason`.
- `splice_e_prev_with_history` **не копирует** `@system` из `E_prev` — только `system_parts`
  текущей дельты → на reasoning попадает свежий JSON последнего `formal_reason` в окне, не
  устаревший payload прошлых циклов.
- Technical failure **вне** текущей дельты не активирует gate (нет relayed `<system>`).

---

## 3. Классификация исхода и gate

Классификация при emit (`compute_formal_reason_outcome` в `formal_reason.py`):

| Условие | `FormalReasonOutcome` |
|---------|------------------------|
| `error_kind` ≠ `NONE` | `technical_failed` |
| supplemental `query` / `derived` error | `technical_failed` |
| `not conforms` или `violations > 0` | `shacl_negative` |
| иначе | `passed` |

| Outcome | Gate | Tools при `remaining_hops ≥ 1` |
|---------|------|--------------------------------|
| `technical_failed` | ON | `formal_reason`, `memory_query` |
| `shacl_negative`, `passed` | OFF | `REASONING_TARGET_STAGES` |

Последний валидный payload — **последний** в `iter_system_parts` с `origin=formal_reason`
(обычно одна часть).

**Приоритет в `reasoning._decide`:** hop budget (`remaining < 1` → только `finalize`) **выше**
gate; при gate ON — `reasoning/formal_reason_gate.j2`; wrong-tool retries упираются в hop
budget, не в отдельный settings-порог.

SHACL negative намеренно **не** включает gate (модель может идти в `response_finalize`).

---

## 4. Строгие инварианты (fail)

| # | Инвариант | Проверка |
|---|-----------|----------|
| I1 | Непустая `<system origin=formal_reason>` на `enrich_fast→reasoning` → валидный `FormalReasonResultPayload` | `require_formal_reason_result_payload` |
| I2 | В дельте был `From: formal_reason@localhost` → на spliced-конверте есть такая `<system>` | `assert_formal_reason_relay_after_splice` в `enrich_fast` |
| I3 | Gate не читает `<history>`, нет legacy без `<system>` | по дизайну |

---

## 5. Код и JSON

| Модуль | Роль |
|--------|------|
| `formal_reason_gate.py` | `formal_reason_gate_active`, strict parse, `delta_had_formal_reason` |
| `states/formal_reason.py` | SHACL/SPARQL, emit observation + result JSON |
| `states/enrich_fast.py` | `_collect_delta_system_parts`, splice, assert relay |
| `states/reasoning.py` | `compute_allowed_routes`, `_decide` |

`FormalReasonResultPayload` в `<system>`: `outcome`, `error_kind`, `conforms`, `violations`,
`has_query_error`, `has_derived_error` — см. [`TYPES.md`](TYPES.md).

Вход стадии: `FormalReasonStagePayload` в единственной `<system>` письма `reasoning →
formal_reason` (`system_part_text`, [`CONTEXT_CONTRACT.md` §2](CONTEXT_CONTRACT.md)).

---

## 6. Промпты

| Файл | Роль |
|------|------|
| `formal_reason/observation_*.j2` | Prose → `<history>` |
| `reasoning/formal_reason_gate.j2` | Notice при gate ON |
| `reasoning/system.j2` | `formal_reason_strategy`, hop budget |
| `reasoning/formal_reason/tool_spec.j2` | Схема tool_call |

---

## 7. Отладка

1. `formal_reason@` → `enrich_fast@`: есть `<system>` с JSON?
2. `enrich_fast@` → `reasoning@`: `<system>` + `X-Threlium-Origin: formal_reason@localhost`?
3. `outcome` и tools в LiteLLM соответствуют таблице §3?

E2e-покрытие (журнал WireMock, `tests/e2e/formal_reason_assertions.py`):

| Сценарий | Модуль |
|----------|--------|
| QUERY ERROR → gate ON → retry `formal_reason` → gate OFF + finalize | `test_formal_reason_technical_gate_e2e.py` |
| `shacl_negative` → gate OFF + finalize | `test_formal_reason_violation_e2e.py` |
| `passed` + `memory_query` без gate | `test_formal_reason_chain_e2e.py` |
| `passed` + query, gate OFF | `test_formal_reason_query_e2e.py` |
| inference success, gate OFF | `test_formal_reason_inference_e2e.py` |
| parse fatal → gate → `memory_query` под gate → QUERY ERROR → recovery → finalize | `test_formal_reason_gate_recovery_matrix_e2e.py` |
