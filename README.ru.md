# Threlium

[English](README.md) | **Русский**

Самохостируемый AI-агент на Unix-примитивах: Maildir, systemd, **fdm**, notmuch. Каналы — почта (IMAP IDLE), Telegram, Matrix. Многошаговое рассуждение через tool calls LLM; долговременная память в LightRAG; shell и правки кода — через контур CLI с политикой и HITL.

## Возможности

- **~20k строк Python** — обработчики FSM и раннеры; конфиги, промпты и Ansible вместо фреймворков
- **FSM на Maildir** — событие = письмо RFC 5322; стадия = `stages/<stage>/Maildir/`
- **Union notmuch index** — одна БД на все stage-Maildir; история в `cur/`, отдельного legacy-архива вне `stages/` нет
- **Оркестрация systemd --user** — `fdm` → `notmuch insert` → `threlium-dispatch.sh` → `threlium-work@` / `threlium-engine`
- **Три канала ввода** — симметричные `threlium.bridges.*` → канонический `ingress@localhost`
- **Три слоя памяти** — контекст треда, глобальные факты, граф LightRAG (RAG-loop в `threlium-engine`)
- **CLI с политикой** — `cli_intent` → `cli_exec` + HITL
- **Субагенты и formal reasoning** — IRT-цепочки, hop budget, SHACL/SPARQL (`formal_reason`)
- **Веб-админка** — Cockpit + Roundcube + Dovecot (трафик агента как почтовые треды)
- **Самомодификация** — локальные коммиты в `threlium_repo_path` через привилегированный `cli_exec`
- **Прод без Docker** — VPS/железо; Docker только для e2e

## Архитектура (кратко)

Мосты один раз нормализуют внешний сигнал в каноническое MIME (`To: ingress@localhost`, `X-Threlium-Route`). Движок `threlium.runners.engine` вызывает handler стадии **in-process**; переход — **`run_fdm`** → терминирующий **`pipe` fdm** → `notmuch insert` + dispatch. Индексация LightRAG — отдельный asyncio-loop того же демона после `nm_settle()`.

Типичный happy path:

`ingress` → `enrich` → `reasoning` → (`egress_router` | память | CLI | субагент | `formal_reason` | response tools | …) → `egress_<channel>` → `archive`

Контракт стадии:

```python
def main(msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings) -> EmailMessage | None:
```

| Слой | Реализация |
| ---- | ---------- |
| Хранилище событий | Durable Maildir в `~/threlium/stages/` |
| Индекс | notmuch2 (union по `stages/*/Maildir`) |
| Маршрутизация | fdm (`~/.fdm.conf`) |
| Оркестрация | systemd --user |
| Рассуждение | litellm + tool calls (ребро FSM не из свободного текста) |
| Память | LightRAG + `thread_memory` / `global_memory` |
| Wire MIME | `threlium.mail`; доменные типы — `threlium.types` (msgspec) |
| Деплой | Ansible (`site.yml`: теги `deploy` / `refresh`) |
| Тесты | только pytest e2e — Compose + baked SUT + WireMock + GreenMail |

Нормативно: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/INDEX.md](docs/INDEX.md), [docs/FSM.md](docs/FSM.md). Развёрнутая статья: [docs/ARTICLE.md](docs/ARTICLE.md).

## Требования

- **Целевой хост:** Ubuntu 24.04+, systemd, `loginctl enable-linger` для пользователя агента
- **Python:** 3.11+ (venv на target; `.venv` в корне репо — dev/e2e)
- **LLM / embeddings:** OpenAI-compatible HTTP
- **IMAP/SMTP:** для почтового канала (в e2e — GreenMail)
- **Control node:** Ansible 2.20+

На target ставятся **fdm**, **notmuch**, **msmtp**, **dovecot**, **cockpit**, **caddy** (см. defaults роли).

## Установка

### 1. Клонирование (control node)

```bash
git clone <repo-url> threlium
cd threlium
python3 -m venv .venv && .venv/bin/pip install -e ".[e2e]"
```

### 2. Inventory

```bash
cp ansible/inventory/hosts.yml ansible/inventory/my-host.yml
```

```yaml
all:
  children:
    threlium_hosts:
      hosts:
        my-server:
          ansible_host: 192.0.2.10
          ansible_user: deploy
```

### 3. Переменные хоста

Файл `ansible/host_vars/my-server.yml`, минимум:

```yaml
threlium_agent_login_password: "your-password"

threlium_litellm:
  llm_endpoints:
    - model: "openai/qwen3-35b"
      api_base: "http://your-vllm-host:8000/v1"
      score: 1.0
  embedding_endpoints:
    - model: "openai/bge-m3"
      api_base: "http://your-vllm-host:8001/v1"

threlium_bridges:
  email:
    imap_host: "imap.example.com"
    imap_user: "agent@example.com"
    imap_pass: "app-password"

threlium_msmtp:
  host: "smtp.example.com"
  port: 587
  user: "agent@example.com"
  password: "app-password"
```

Полная карта переменных: [docs/PLAYBOOK.md](docs/PLAYBOOK.md).

### 4. Деплой

```bash
ansible-playbook ansible/playbooks/site.yml \
  -i ansible/inventory/my-host.yml \
  -e @ansible/host_vars/my-server.yml \
  --tags deploy
```

### 5. Обновление кода (без apt/веб-стека)

```bash
ansible-playbook ansible/playbooks/site.yml \
  -i ansible/inventory/my-host.yml \
  --tags refresh
```

Повторный полный `deploy` на живом хосте — **disaster recovery** (затирает локальный дрейф в `threlium_repo_path`). Штатные правки на target — локальные коммиты или `refresh` с control node.

## Использование

- **Почта** — письмо на адрес агента; ответ в том же треде
- **Telegram / Matrix** — включить в `threlium_bridges` и unit'ах мостов

**Веб:** `https://<host>:9090` (Cockpit), `http://<host>:8080/webmail/` (Roundcube).

```bash
# На target от пользователя threlium:
systemctl --user status threlium-engine.service
journalctl --user -u threlium-engine.service -f
```

## Тестирование

Единственный автоматизированный слой — e2e ([docs/TESTING.md](docs/TESTING.md)):

```bash
.venv/bin/pip install -e ".[e2e]"

# Первый раз или после смены плейбука/пакетов — запекание образа SUT:
.venv/bin/pytest -n0 tests/e2e/wipe_bake.py

# Сценарии (общий Compose: sut + greenmail + wiremock):
.venv/bin/pytest tests/e2e

# Последовательный прогон с логами на тест:
./test-runs/run_individual_e2e.sh
```

После правок RFC822 / `threlium.mail`: `scripts/check_mail_wire.sh`.

## Структура репозитория

```
ansible/
  playbooks/site.yml           # deploy + refresh
  roles/threlium/
    files/scripts/threlium/    # FSM, bridges, runners, types, mail/
    files/prompts/             # промпты Jinja2
    files/knowledge/           # bootstrap для LightRAG
    templates/                 # threlium.yaml, fdm.conf, systemd
tests/e2e/                     # pytest + compose + wiremock_stubs/
docs/                          # контракты архитектуры
```

## Документация

| Документ | Тема |
| -------- | ---- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Обзор системы |
| [docs/INDEX.md](docs/INDEX.md) | Хранилище, notmuch, LightRAG |
| [docs/FSM.md](docs/FSM.md) | Граф стадий и контракт handler |
| [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md) | systemd, dispatch, параллелизм |
| [docs/PLAYBOOK.md](docs/PLAYBOOK.md) | Ansible-деплой |
| [docs/TESTING.md](docs/TESTING.md) | E2e harness |
| [docs/TYPES.md](docs/TYPES.md) | msgspec / wire-типы |
| [docs/ARTICLE.md](docs/ARTICLE.md) | Развёрнутая статья с диаграммами |
