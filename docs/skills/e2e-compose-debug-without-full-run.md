# Отладка e2e-стека (compose) без полного pytest и без правок в проекте

Полный цикл mailflow e2e (pytest, WireMock, стабы, порядок расследования FAIL) — [`mailflow-e2e-wiremock-sut.md`](mailflow-e2e-wiremock-sut.md).

Инструкция для агента: как локализовать причину сбоя mailflow / FSM / LLM, опираясь на **уже поднятый** compose (`sut`, `greenmail`, `wiremock`), **не** запуская полный `pytest -n0 tests/e2e/wipe_bake.py` и **не** меняя дерево репозитория (если политика такая). Правки допустимы **внутри контейнеров** и временная подмена маппингов WireMock через Admin API / файлы под `tests/e2e/wiremock_stubs/`, когда это явно разрешено.

## 0. Дисциплина: не останавливаться и не перекладывать ответственность

Разрешена полная свобода действий **внутри контейнеров** (SUT, GreenMail, образы compose) и временные правки стабов WireMock (через Admin API или каталог `tests/e2e/wiremock_stubs/`, если политика явно разрешает трогать репозиторий). Это **безопасно** для продуктового кода и для **сценариев/хелперов тестов в репозитории** — их не трогаем, пока политика явно не разрешает.

- **Пока вручную не достигнута полная работоспособность целевого тестового сценария** (критерии закрытия цикла — §6: notmuch-тред, IMAP-ответ и т.д.), **останавливать работу не нужно** и **не спрашивать пользователя**, продолжать ли или «достаточно ли анализа».
- **Не отмахиваться** формулировками вроде «это не моя проблема» / «виноват внешний компонент»: пока в зоне mock и контейнеров можно устранить или обойти сбой — **нужно довести сценарий до прохода**; если упёрлись в жёсткий предел среды — зафиксировать его **после** того, как исчерпаны допустимые правки и есть чёткие логи/шаги воспроизведения.
- **Граница правок:** только **mock** или **файлы в контейнерах**; остальной репозиторий (в т.ч. тела тестов под `tests/e2e/*.py`) не менять без отдельного разрешения.

## 1. Зафиксировать контекст стека

- `docker ps --format '{{.Names}}' | grep -E sut|greenmail|wiremock'` — имена контейнеров и compose-проект по префиксу `threlium_e2e_shared_*`.
- Порты на хосте: `docker port <greenmail> 3025 3143` (SMTP / plain IMAP для сценариев с хоста).
- В SUT пути по умолчанию: `THRELIUM_HOME` ≈ `/home/threlium/threlium/data`, код агента ≈ `/home/threlium/threlium/agent`, notmuch: `HOME=/home/threlium`, `NOTMUCH_CONFIG=/home/threlium/.notmuch-config`.

## 2. Воспроизвести сценарий вручную, как в тесте

- Прочитать **только** `tests/e2e/test_*.py` и `tests/e2e/helpers.py` / `smtp_inject.py` — что именно инжектится (From, To, Message-ID, Subject), какой **якорь** для notmuch (`email_ingress_notmuch_id_inner` и т.д.).
- Инъекция с хоста: `python tests/e2e/smtp_inject.py 127.0.0.1 <smtp_port> --message-id … --subject …` (как в mailflow).
- Якорь `id:` для notmuch посчитать локально через `PYTHONPATH=ansible/roles/threlium/files/scripts` и `threlium.types.rfc.RfcMessageIdWire.from_inner_for_bridge` (или импорт хелперов из `tests/e2e`, если удобнее) — **не** дублировать логику на глаз.

## 3. Сужать круг: notmuch → Maildir → воркер → внешняя служба

1. **Notmuch в SUT** (`docker exec -u threlium <sut> bash -lc 'export HOME=… NOTMUCH_CONFIG=…'`):
   - `notmuch search --limit=1 --output=threads "id:<anchor>"` → `tid`;
   - `notmuch count <tid>` и `notmuch count "<tid> and not tag:unread"` — рассинхрон = «кто-то с `unread`»;
   - `notmuch search --output=messages "<tid> and tag:unread"` — конкретные id;
   - `notmuch search --output=files "id:<mid>"` — путь в `stages/.../Maildir/new` или `cur`.

2. **Застряло в `new/`** — почти всегда «воркер не дописал / упал до снятия `unread`».

3. **Systemd (user)** в SUT: `XDG_RUNTIME_DIR=/run/user/$(id -u threlium)` и `systemctl --user list-units 'threlium-work@*' --all` — искать **failed** по стадии из пути (`egress_email`, …).

4. Если `journalctl --user` недоступен из-под `threlium`, смотреть **системный** журнал с хоста через `docker exec` (root):  
   `journalctl -b --no-pager | grep threlium-work` — там часто полный traceback и сообщение внешнего процесса (**msmtp**, **python**).

5. **Соседний контейнер** (типично **GreenMail**): `docker logs <greenmail> 2>&1 | tail -…` — причины `451`, `550`, исключения Java часто **точнее**, чем код выхода msmtp на SUT.

## 4. Гипотезы по слоям (порядок проверки)

| Слой | Что смотреть |
|------|----------------|
| Доставка наружу | `egress_email`, `~/.msmtprc`, логи GreenMail на SMTP |
| Индексация / LLM | `threlium-engine` (RAG-loop + LLM в enrich), журнал WireMock `GET /__admin/requests`, POST `/embeddings` / `/chat/completions` |
| Мост / вход | `threlium-bridge@email`, fetchmail, IMAP Seen на mapped порту |

Не смешивать слои: сначала довести до зелёного **тот шаг, который падает** (например доставка ответа), потом уже LightRAG.

## 5. Правки при запрете на репозиторий

- Менять файлы **внутри SUT** под `/home/threlium/threlium/agent/scripts/...` (как после `ansible sync`), либо временно добавлять маппинги WireMock через `curl` к `http://127.0.0.1:<mapped 9080→8080>/__admin/...`.
- После правки **одной** точки — минимальная проверка: ручной запуск воркера  
  `docker exec -u threlium … bash -lc 'cd …/agent && . .venv/bin/activate && set -a && . env/threlium.env && set +a && python -m threlium.runners.worker <stage>:<thread_id>'`  
  или повтор инъекции + короткий **poll** notmuch (цикл `sleep` + `notmuch count`, без pytest).

## 6. Критерий «цикл закрыт» (аналог mailflow без pytest)

- Тред по якорю: `full == settled` (все сообщения без `unread`).
- Для ответа: IMAP на mapped порту в ящик **pytest** (или как в тесте) — письмо с ожидаемым Subject/body и, при корреляции, **`In-Reply-To`** = inner исходного `Message-ID` инъекции (тот же, что в `--message-id` / `raw_id` в хелперах); на внешнем SMTP служебных `X-Threlium-*` нет.

## 7. Чего не делать

- Не гонять **`pytest -n0 tests/e2e/wipe_bake.py`**, если задача — локализовать регрессию на уже собранном baked-образе: достаточно compose + ручные шаги + при необходимости **идемпотентный** `site.yml` (это отдельное решение пользователя/CI).
- Не раздувать diff в репозитории, если политика «только контейнер / только mock»: фиксацию в git делает человек после ревью.

## 8. Краткий чеклист

1. `docker ps` + `docker port` для GreenMail.  
2. Читать сценарий из тестов (инжект, якоря).  
3. `smtp_inject` → notmuch tid / unread → файл в Maildir.  
4. `systemctl --user` + `journalctl` (system/user) + **`docker logs` GreenMail**.  
5. Патч в контейнере / mock → ручной worker или короткий poll.  
6. Подтвердить IMAP + notmuch «полный тред».

Такой проход от **симптома assert** к **корневой ошибке внешней системы** обычно быстрее полного e2e и не трогает проект до осознанного коммита.
