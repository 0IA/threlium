# Threlium: модель тредов — внешний «диалог с агентом» и внутренний линейный FSM-тред

Документ фиксирует **ключевой инвариант целостности тредов** Threlium: одновременно
поддерживаются два согласованных представления одной и той же беседы:

1. **Внешний почтовый тред пользователя** («диалог с агентом») — то, что видит MUA
   пользователя (или клиент Telegram / Matrix). В нём только письма пользователя и
   ответы агента; внутренних FSM-сообщений нет. Тред короткий и человекочитаемый.
2. **Внутренний FSM-тред** — полная цепочка в union-notmuch, **строго линейная по
   `In-Reply-To`**, включающая **все** FSM-сообщения (`ingress → enrich → summarize_* →
   reasoning → tasks_upsert → response_finalize → egress_router → egress_* → archive`).
   Письма пользователя в этой полной цепочке отмечены тегом `tag:route`.

Связующий механизм между двумя представлениями — **glue-запись в `archive`** (см.
[`egress_self_archive.py`](../ansible/roles/threlium/files/scripts/threlium/egress_self_archive.py)).
Это **крайне важный инвариант подхода**: его нарушение ломает наследование контекста и
task-ledger между ходами пользователя.

Смежные контракты: [`MESSAGES.md` §2](MESSAGES.md#2-канонизация-идентификаторов-на-границах-системы)
(канонизация MID/IRT на границах), [`FSM.md`](FSM.md) (граф стадий, билдеры MIME),
[`SUBAGENT_TABLE.md`](SUBAGENT_TABLE.md) (маркеры `subagent_intent`/`subagent_end`,
изоляция уровней), [`RESPONSE_TABLE.md`](RESPONSE_TABLE.md) (response-буфер и task-CRDT по
IRT), [`ARCHITECTURE.md` §5.1.1](ARCHITECTURE.md#511-отказ-от-линейных-цепочек-и-форков-треда)
(почему **контекст** RAG берётся по всему tread'у, а **маршрутизация/ledger** — по линейной
цепочке).

---

## 1. Два представления одного диалога

### 1.1. Внешний почтовый тред (что видит пользователь)

В клиенте пользователя ответ агента тредится **на письмо пользователя**. Внешний
`Message-ID` ответа назначается каналом доставки (для email — `make_msgid`, для Telegram /
Matrix — API-присвоенный `message_id` / `event_id`), внешний `In-Reply-To` —
`reply_target_rfc_message_id` маршрута (исходный внешний MID письма пользователя):

```text
[U1] письмо пользователя          (Message-ID: ext-U1)
  └─ [A1] ответ агента            (In-Reply-To: ext-U1, Message-ID: ext-A1)
       └─ [U2] ответ пользователя (In-Reply-To: ext-A1, Message-ID: ext-U2)
            └─ [A2] ответ агента   (In-Reply-To: ext-U2, …)
```

В этом представлении **меньше** писем и **нет** внутреннего FSM-содержимого — это и есть
человеческий «диалог с агентом».

### 1.2. Внутренний FSM-тред (полная линейная цепочка)

В union-notmuch та же беседа — **одна линейная цепочка `In-Reply-To`**, в которой
присутствуют все FSM-сообщения, а `tag:route` отмечает узлы пользователя:

```text
ingress(U1)  [tag:route]
  → enrich → summarize_context → summarize_memory → reasoning
  → tasks_upsert → reasoning → response_finalize → egress_router → egress_email
  → archive(GLUE A1)            [Message-ID = canon(ext-A1)]
       → ingress(U2)  [tag:route]   (In-Reply-To → GLUE A1)
       → enrich → … → response_finalize → egress_router → egress_email
       → archive(GLUE A2)       [Message-ID = canon(ext-A2)]
            → …
```

Каждый переход стадии строит новое письмо билдером
[`build_fsm_plain_to_stage`](../ansible/roles/threlium/files/scripts/threlium/fsm_emit.py)
/ `emit_transition_*_preserving_payload`, выставляя `In-Reply-To` = `Message-ID` **своего
входа**. Поэтому цепочка непрерывна и линейна на **каждом** шаге, без исключений.

---

## 2. Glue-запись: мост между внешним MID и внутренней линейностью

Внешние идентификаторы (`ext-A1`) присваиваются **вне** Threlium (MTA / API канала), и
именно на них пользователь ставит `In-Reply-To`, когда отвечает. Чтобы его ответ вернулся
во внутренний тред **в нужную точку**, при отправке egress пишет запись в `archive` с
`Message-ID`, равным **канонической форме внешнего MID**:

* `egress_email` (см. [`states/egress_email.py`](../ansible/roles/threlium/files/scripts/threlium/states/egress_email.py)):
  `outbound_mid = make_msgid(...)` → внешнее SMTP-письмо уходит с `Message-ID: ext-A1`;
  glue-запись получает `Message-ID = RfcMessageIdWire.from_native(EmailNativeId(v=1,
  message_id=ext-A1))` = `canon(ext-A1)`.
* Telegram / Matrix: `glue_message_id_wire` строится из API-присвоенного `message_id` /
  `event_id` (см. [`egress_self_archive.build_egress_sent_record_to_archive`](../ansible/roles/threlium/files/scripts/threlium/egress_self_archive.py)).

Когда пользователь отвечает (`In-Reply-To: ext-A1`), мост канонизирует внешний MID в ту же
`canon(ext-A1)` ([`MESSAGES.md` §2](MESSAGES.md#2-канонизация-идентификаторов-на-границах-системы)),
и новое `ingress(U2)` находит в индексе именно **glue-запись** — продолжая внутренний
линейный тред. Так:

* **внешний** почтовый тред пользователя цел (ответ тредится на письмо пользователя);
* **внутренний** FSM-тред цел и линеен (glue замыкает ответ пользователя на полную цепочку
  предыдущего хода — вплоть до `tasks_upsert`, `response_finalize` и т.д.).

---

## 3. Инвариант линейности `In-Reply-To` (ключевой)

> **Подъём по `In-Reply-To` от листа — это линейная ветка**, а не обход всего дерева
> notmuch-треда. Маршрутизация возврата, резолв канала, сбор task-ledger, сбор
> response-буфера и изоляция уровней субагентов — **все** опираются на эту линейность.

Почему **нельзя** собирать «всё дерево треда со всеми форками»: пользователь (или процесс)
может ответить на **любое** письмо, создав форк. Один notmuch-`thread:` объединяет
множество таких веток. Обход всего дерева смешал бы независимые ветки и разрушил бы
однозначность «беседы с агентом и его работы». Подъём по `In-Reply-To` корректно
выделяет **ровно одну** линейную ветку внутри общего notmuch-треда — ту, на которой мы
сейчас работаем.

Реализации, опирающиеся на инвариант:

* **Маршрут/канал egress**: [`resolve_route_from_in_reply_to_ancestors`](../ansible/roles/threlium/files/scripts/threlium/ingress_route_resolve.py)
  — подъём по `In-Reply-To` до первого предка с `tag:route` и непустым `X-Threlium-Route`.
* **task-ledger**: [`task/collect.collect_task_ops`](../ansible/roles/threlium/files/scripts/threlium/task/collect.py)
  — подъём по фрейму (без остановки на `tag:route`: ledger переживает ходы пользователя
  внутри фрейма), сбор `task-init` / `tasks_upsert`.
* **response-буфер**: [`response/collect`](../ansible/roles/threlium/files/scripts/threlium/response/collect.py)
  — тот же подъём, но `stop_at_route=True` (буфер живёт per-turn).
* **изоляция субагентов**: [`thread_context_filter.iter_irt_ancestors_filtered`](../ansible/roles/threlium/files/scripts/threlium/thread_context_filter.py)
  — единый `skip_counter` по балансу маркеров `subagent_intent`/`subagent_end`; корректен
  **только** при линейном `In-Reply-To`.

(Исключение — **контекст RAG/enrich**: он намеренно берётся по всему RFC-треду со всеми
форками, см. [`ARCHITECTURE.md` §5.1.1](ARCHITECTURE.md#511-отказ-от-линейных-цепочек-и-форков-треда).
Это не противоречит данному инварианту: маршрутизация и ledger — линейны, семантический
контекст — тред-wide.)

---

## 4. Следствия для egress (терминальный шаг)

Терминальный egress (`response_finalize → egress_router → egress_*`) обязан соблюдать
линейность так же, как любой другой переход:

* **Внутренний `In-Reply-To`** письма к `egress_*` ставится на **FSM-вход**
  (`response_finalize`-выход) через `emit_transition_simple_step_preserving_payload`
  (см. [`states/egress_router.py`](../ansible/roles/threlium/files/scripts/threlium/states/egress_router.py)).
  **Запрещено** перетредивать терминальный egress на route-MID — это создаёт «шорткат»
  мимо ветки `reasoning → tasks_upsert → response_finalize` и рвёт линейность внутреннего
  треда (glue унаследует укороченную цепочку, и task-ledger прошлого хода перестаёт
  наследоваться).
* **Внешний `In-Reply-To` / `References`** SMTP-письма строятся **отдельно** из
  route-payload (`reply_target_rfc_message_id`, `References` route-предка) в
  [`egress_email._build_smtp_message`](../ansible/roles/threlium/files/scripts/threlium/states/egress_email.py)
  — независимо от внутреннего заголовка. Поэтому внешнее тредирование пользователя не
  страдает от линейного внутреннего трединга.
* **Канал/получатель** резолвятся подъёмом по `In-Reply-To` до `tag:route`-предка
  (находится при линейном трединге так же, просто на несколько хопов выше), канал на
  конверт к `egress_*` **не** кладётся (см. [`FSM.md` §138](FSM.md)).

Субагентный возврат (`depth>0 → subagent_end`) и HITL-bridge (`cli_hitl_out`) уже линейны
(тот же `emit_transition_simple_step_preserving_payload`).

---

## 5. Чеклист инварианта (что должно соблюдаться)

* [ ] Каждое FSM-письмо тредится на свой вход → внутренний тред линеен по `In-Reply-To`.
* [ ] Терминальный egress **не** перетредивается на route-MID.
* [ ] glue-запись в `archive` имеет `Message-ID = canon(внешний MID)` и `In-Reply-To` на
      egress-задачу (линейно).
* [ ] Внешний `In-Reply-To`/`References` ответа агента — из route-payload, отдельно от
      внутреннего заголовка.
* [ ] Подъём по `In-Reply-To` от листа любого хода доходит до `tag:route` предыдущих ходов
      **через** их `tasks_upsert` / `response_finalize` (а не мимо них).
* [ ] Резолв маршрута, сбор ledger/буфера и изоляция субагентов используют линейный подъём,
      а не обход всего дерева.

Проверка вручную — подъём по `In-Reply-To` от листа последнего хода в SUT (через
`notmuch find` по `Message-ID` и чтению `In-Reply-To`) должен показывать непрерывную
линейную цепочку через все стадии всех ходов, с `glue`-записями между ходами.
