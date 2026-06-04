# FSTS — быстрый синк SUT без Ansible

**FSTS** (fast SUT sync): обновить на уже поднятом e2e-стеке Python-пакет `threlium`, Jinja-промпты и при необходимости очистить локальное состояние почты и журнал WireMock — **без** `ansible-playbook`, rebake образа и `wipe_sync` / `wipe_bake`.

Инварианты и полный контур — [TESTING.md](TESTING.md), [PLAYBOOK.md §2.1](PLAYBOOK.md#21-текущее-поведение-повторного-прогона-и-его-границы). Здесь только ручной «инкремент» для итерации по коду.

---

## Предусловия

- Docker Compose с сервисами `sut`, `greenmail`, `wiremock` уже запущен (тот же `tests/e2e/compose/docker-compose.yml`, что в harness).
- На **хосте** известен каталог репозозитория (`REPO_ROOT`); в примерах ниже: `/path/to/threlium`.
- Порты: WireMock Admin обычно на хосте `127.0.0.1:9080` (см. проброс в compose); `sut` — `docker ps` по сервису `sut`.

Пути **внутри контейнера `sut`** по умолчанию совпадают с переменными из [`tests/e2e/toolkit/constants.py`](../tests/e2e/toolkit/constants.py) (`THRELIUM_E2E_REMOTE_*` при необходимости переопределяют):

| Назначение | Путь в SUT |
|------------|------------|
| Клон / агентский корень | `/home/threlium/threlium/agent` (`THRELIUM_E2E_REMOTE_REPO_PATH`) |
| Пакет `threlium` (editable) | `/home/threlium/threlium/agent/scripts/threlium` |
| `pyproject.toml` пакета | `/home/threlium/threlium/agent/scripts/pyproject.toml` |
| venv для `pip` / runtime | `/home/threlium/threlium/agent/.venv` (**не** `scripts/.venv`) |
| `THRELIUM_HOME` / данные FSM | `/home/threlium/threlium/data` |
| Промпты | `/home/threlium/threlium/data/prompts` |

Идентификатор контейнера `sut`:

```bash
docker ps --filter label=com.docker.compose.service=sut --format '{{.ID}}'
```

Дальше в примерах переменная `CID` — короткий или полный id этого контейнера.

---

## 1. Очистка журнала WireMock и state extension (опционально)

На **общем** инстансе WireMock полный сброс журнала задевает параллельные прогоны; для одиночной машины разработчика обычно допустимо.

```bash
WM=http://127.0.0.1:9080   # подставьте фактический host:port с хоста
curl -sS -X DELETE "$WM/__admin/requests" -o /dev/null -w "journal %{http_code}\n"
curl -sS -X DELETE "$WM/__admin/state-extension/contexts" -o /dev/null -w "state %{http_code}\n"
curl -sS -X POST "$WM/__admin/scenarios/reset" -o /dev/null -w "scenarios %{http_code}\n"
```

**Маппинги** (`/__admin/mappings`) этими вызовами не удаляются. Перед live-pytest сценарий сам поднимает нужные стабы (`_live_prepare_wiremock` в [`test_mailflow_live_only_e2e.py`](../tests/e2e/test_mailflow_live_only_e2e.py): bootstrap + каталог `wiremock_stubs/...` + сид state). Если после ручной чистки state тесты с `state-matcher` падают с «custom matcher does not match», прогоните нужный live-тест (он пересидирует контекст) или вручную повторите upsert из `wiremock_client` / pytest.

---

## 2. Сброс Maildir стадий и `notmuch new` (опционально)

Та же политика, что [`e2e_flush_sut_fsm_maildirs`](../tests/e2e/toolkit/cleanup.py) в e2e harness: удалить файлы писем в `*/Maildir/{new,cur,tmp}/*` под `$TH/stages`, затем от пользователя `threlium` выполнить `notmuch new` (индекс `stages/.notmuch` не удаляем — полный wipe ломает окружение).

```bash
TH=/home/threlium/threlium/data
docker exec "$CID" bash -lc "set -eu
if [ -d \"$TH/stages\" ]; then
  find \"$TH/stages\" \\( -path '*/Maildir/new/*' -o -path '*/Maildir/cur/*' -o -path '*/Maildir/tmp/*' \\) -type f ! -name '.*' -delete 2>/dev/null || true
fi
su - threlium -s /bin/bash -c 'export HOME=/home/threlium NOTMUCH_CONFIG=/home/threlium/.notmuch-config; notmuch new' </dev/null || true
echo done
"
```

Пропуск из тестов: `THRELIUM_E2E_LIVE_SKIP_SUT_MAILDIR_FLUSH=1` (см. тот же модуль).

---

## 3. Копирование дерева с хоста (`docker cp`)

Источник в git — роль Ansible `files/` (то, что и так кладёт `site.yml`):

```bash
REPO=/path/to/threlium
AGENT_SCR=/home/threlium/threlium/agent/scripts
DATA_PROMPTS=/home/threlium/threlium/data/prompts

docker cp "$REPO/ansible/roles/threlium/files/scripts/threlium/." "$CID:$AGENT_SCR/threlium/"
docker cp "$REPO/ansible/roles/threlium/files/scripts/pyproject.toml" "$CID:$AGENT_SCR/pyproject.toml"
docker cp "$REPO/ansible/roles/threlium/files/prompts/." "$CID:$DATA_PROMPTS/"

docker exec "$CID" chown -R threlium:threlium \
  "$AGENT_SCR/threlium" "$AGENT_SCR/pyproject.toml" "$DATA_PROMPTS"
```

Шаблоны `*.j2` в репозитории лежат под `ansible/roles/threlium/files/prompts/` — после копирования движок читает их из `$THRELIUM_HOME/prompts` (в e2e это `.../data/prompts`).

---

## 4. Переустановка editable-пакета в venv SUT

```bash
docker exec "$CID" bash -lc \
  'su - threlium -s /bin/bash -c "/home/threlium/threlium/agent/.venv/bin/pip install -e /home/threlium/threlium/agent/scripts -q"'
```

Без этого Python может продолжать импортировать старые `.pyc` из установленного snapshot’а; `-e` подхватывает обновлённые файлы на диске.

---

## 5. Перезапуск user systemd в SUT

После смены кода стадий / раннеров перезапустите движок и мост (для канала email в e2e):

```bash
docker exec "$CID" bash -lc \
  'su - threlium -s /bin/bash -c "export XDG_RUNTIME_DIR=/run/user/\$(id -u); \
    systemctl --user restart threlium-engine.service; \
    systemctl --user restart threlium-bridge@email.service"'
```

Другие каналы — свои `threlium-bridge@….service`, если включены вне дефолтного e2e.

---

## 6. Проверка

```bash
docker exec "$CID" bash -lc \
  'su - threlium -s /bin/bash -c "export XDG_RUNTIME_DIR=/run/user/\$(id -u); systemctl --user --state=running list-units threlium-engine.service threlium-bridge@email.service"'
```

Дальше — целевой pytest, например live mailflow (см. [TESTING.md §5–7](TESTING.md)).

---

## Что FSTS **не** заменяет

- Изменения в systemd unit-файлах, `fdm.conf`, системных пакетов, образа контейнера — по-прежнему `site.yml` / rebake.
- Полная пересборка зависимостей Python (новые пакеты в `pyproject.toml`) — в SUT нужен `pip install` нужных пакетов в тот же `.venv` (или снова плейбук).
- Согласование версии образа `sut` с веткой репозитория — вручную или через bake.
