# Как добавить новую стадию в FSM (пошагово)

Практическая процедура для стадии с mailbox **`<stage_id>@localhost`** и очередью **`stages/<stage_id>/Maildir`**. Нормативный контракт стадии, граф и билдеры — [`FSM.md`](FSM.md); **fdm** и notmuch — [`MESSAGES.md` §3](MESSAGES.md#3-mailfilter-snippet); оркестрация воркеров — [`ORCHESTRATION.md` §3](ORCHESTRATION.md#3-механизм-post-insert-hook--dispatch-script).

Имя стадии — это **один** идентификатор `snake_case` (как local-part адреса и имя Python-модуля): например `my_stage` → `my_stage@localhost`, файл `my_stage.py`.

---

## 1. Выбрать идентификатор и место в графе

1. Зафиксировать **`stage_id`** (только `[a-z0-9_]`, без `+` в адресе).
2. Определить, **кто** эмитит письмо на `To: <stage_id>@localhost` (предыдущая стадия, мост только для `ingress`, или новый tool-call в `reasoning` — см. [`SUBAGENT_TABLE.md`](SUBAGENT_TABLE.md)).
3. Решить семантику выхода handler'а:
   - **`EmailMessage`** — переход дальше: движок вызовет **`run_fdm`** → **fdm** сделает `notmuch insert` в Maildir следующей стадии (см. [`FSM.md` §4.1](FSM.md#41-контракт-handler-а)).
   - **`None`** — терминал на этой стадии (как **`archive`**): второго **`run_fdm`** нет, выполняется **`nm_settle`** входного файла. Для обычной «обрабатывающей» стадии почти всегда нужен **`EmailMessage`**.

---

## 2. Python: перечисление стадии (`FsmStage`)

Файл: [`ansible/roles/threlium/files/scripts/threlium/types/fsm_stage.py`](../ansible/roles/threlium/files/scripts/threlium/types/fsm_stage.py).

1. Добавить член перечисления, например **`MY_STAGE = "my_stage"`**, сразу под остальными.
2. В комментарии в начале файла указано: состав **должен** совпадать с Ansible **`threlium_fsm_mailbox_stages[].id`** — не забыть шаг 4.

---

## 3. Python: модуль стадии

Файл: **`ansible/roles/threlium/files/scripts/threlium/states/<stage_id>.py`**.

1. Реализовать **`def main(msg: EmailMessage, stage: FsmStage, *, config: Config) -> EmailMessage | None`** — сигнатура и запреты транспорта — [`FSM.md` §4–§4.5](FSM.md#4-контракт-стадии-handler-main).
2. Собирать исходящий MIME через билдеры из **`threlium.fsm_emit`** (`emit_transition_preserving_payload`, `build_fsm_plain_to_stage`, …) — не писать в Maildir и не вызывать **`run_fdm`** из стадии.
3. При необходимости добавить Jinja под **`$THRELIUM_HOME/prompts/<stage_id>/`** (деплой из `ansible/roles/threlium/files/prompts/`).

---

## 4. Python: реестр воркера

Файл: [`ansible/roles/threlium/files/scripts/threlium/states/registry.py`](../ansible/roles/threlium/files/scripts/threlium/states/registry.py).

1. **`from threlium.states import …, <module>`** — импорт модуля с `main`.
2. В **`STAGE_MAIN_MODULES`** добавить строку **`FsmStage.<ENUM>: <module>`**.
3. При импорте пакета выполняется проверка **`set(STAGE_MAIN_MODULES) == set(FsmStage)`** — если забыли enum или словарь, процесс упадёт с понятной ошибкой.

---

## 5. Ansible: список mailbox-стадий

Файл: [`ansible/roles/threlium/vars/main.yml`](../ansible/roles/threlium/vars/main.yml).

В список **`threlium_fsm_mailbox_stages`** добавить элемент:

```yaml
- { id: <stage_id> }
```

Имя файла handler'а не задаётся здесь — по конвенции это **`states/<stage_id>.py`** (шаг 3).

От него автоматически строится **`threlium_maildir_rel_paths`** (`stages/<id>/Maildir`) для bootstrap Maildir в плейбуке — отдельный one-off task для каталога обычно не нужен.

---

## 6. Шаблон **fdm** (`~/.fdm.conf`)

Файл: [`ansible/roles/threlium/templates/config/fdm.conf.j2`](../ansible/roles/threlium/templates/config/fdm.conf.j2).

### 6.1. Обычная стадия (только `notmuch insert` в свой Maildir)

Если стадия **не** `ingress` и **не** `archive`, для неё генерируются:

- **`action "ins_stage_<stage_id>"`** — цикл **`{% for s in threlium_fsm_mailbox_stages %}`** с условием **`s.id != 'ingress' and s.id != 'archive'`**;
- **`match "^To:.*<stage_id>@localhost"`** → это действие — тот же цикл в блоке **`match account "stdin"`**.

Достаточно добавить стадию в **`main.yml`** (шаг 5) и перераскатить роль: строки для новой стадии появятся из шаблона.

### 6.2. Особые случаи (править шаблон руками)

- **`ingress`** — отдельные действия для мостов и тега **`+route`**; не трогать общий цикл без необходимости.
- **`archive`** — отдельное действие **`ins_stage_archive`** с **`+lightrag_indexed`** на insert (чтобы RAG-loop не забирал письма в pending). Любая новая стадия с **аналогичной** семантикой потребует **своего** именованного `action` и **`match` выше** общих правил, плюс исключение из цикла, как у `archive`.

Порядок **`match`** важен: узкие правила (конкретный адресат / bridge) должны быть **выше** общих.

---

## 7. Маршрутизация в графе (если участвует `reasoning`)

Если стадия — цель **tool call** LLM:

1. Обновить спецификацию tools / матрицу — [`SUBAGENT_TABLE.md`](SUBAGENT_TABLE.md), при необходимости код загрузки tools в **`reasoning`** и валидацию аргументов.
2. Убедиться, что билдер исходящего письма ставит ровно **`To: <stage_id>@localhost`** (инвариант [`FSM.md` §4.2](FSM.md#42-что-делает-воркер-перед-вызовом-handler-а)).

Для веток **`ingress_router` / `egress_router`** — те же документы и соответствующие модули в **`threlium/states/`**.

---

## 8. Документация и типы

1. Строка в таблице стадий [`FSM.md` §2.1](FSM.md#21-канонический-состав-стадий-threlium_fsm_mailbox_stages) (краткая роль).
2. При появлении новых доменных VO — [`docs/TYPES.md`](TYPES.md); при влиянии на почтовый wire — [`docs/MESSAGES.md`](MESSAGES.md).
3. Если меняется операционный чеклист деплоя — точечно [`docs/PLAYBOOK.md`](PLAYBOOK.md).

---

## 9. Тесты и проверка

1. **Unit:** вызов **`main(...)`** с минимальным **`EmailMessage`** и узкий **`Config(...)`**; при необходимости **`THRELIUM_HOME`** и фикстура **`prompts/`** (см. существующие тесты стадий).
2. После деплоя: тестовый **`run_fdm`** с RFC822, где **`To: <stage_id>@localhost`**, и убедиться, что **`notmuch`** видит письмо в **`folder:<stage_id>/Maildir`** и что **`threlium-dispatch.sh`** поднимает **`threlium-work@<stage_id>:…`** ([`ORCHESTRATION.md`](ORCHESTRATION.md)).

---

## Контрольный чеклист

| Шаг | Артефакт |
|-----|----------|
| Enum | `FsmStage` в `types/fsm_stage.py` |
| Handler | `states/<stage_id>.py` → `main` |
| Реестр | `states/registry.py` → импорт + `STAGE_MAIN_MODULES` |
| Ansible | `vars/main.yml` → `threlium_fsm_mailbox_stages` |
| fdm | для типовой стадии — только переразкатка `fdm.conf.j2`; иначе — отдельный `action`/`match` |
| Граф | при необходимости `reasoning` / роутеры / `SUBAGENT_TABLE.md` |
| Доки | `FSM.md` §2.1 + при необходимости `MESSAGES` / `PLAYBOOK` |
| Тесты | новые сценарии — каталог `tests/e2e/wiremock_stubs/<…>/` + модуль в `tests/e2e/` ([E2E.md](E2E.md), [E2E.md](E2E.md)) |

После изменений локально: регрессия контура — **`pytest tests/e2e/…`**; инвариант **`set(STAGE_MAIN_MODULES) == set(FsmStage)`** ловит рассинхрон enum и реестра без отдельного теста.
