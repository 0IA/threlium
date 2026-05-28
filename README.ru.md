# Threlium

[English](README.md) | **Русский**

Самохостный AI-агент, построенный из Unix-примитивов: Maildir, systemd, fdm, notmuch. Общается через Email (IMAP), Telegram и Matrix. Рассуждает многошагово с помощью LLM и tool calls, хранит долговременную память в графе знаний (LightRAG), может выполнять shell-команды и модифицировать собственный код.

Подробная статья об архитектуре: [docs/ARTICLE.md](docs/ARTICLE.md)

## Особенности

- **~6k строк Python** — минимум кода, конфиги и скрипты вместо фреймворков
- **FSM на Maildir'ах** — каждое событие = RFC 5322 письмо, каждая стадия = папка на диске
- **Оркестрация через systemd --user** — без Celery, RabbitMQ, Kubernetes
- **Три канала ввода/вывода** — Email (IMAP IDLE), Telegram (Bot API), Matrix (nio)
- **Трёхслойная память** — тред, глобальные факты, LightRAG (граф знаний)
- **CLI с политикой безопасности** — разделение решение/политика/исполнение + HITL
- **Субагенты** — рекурсивные вызовы через IRT-цепочки
- **Веб-админка** — Cockpit + Roundcube + Dovecot (мысли агента видны как почтовые треды)
- **Самомодификация** — агент может править свои промпты, конфиги и код
- **Минимальные ресурсы** — влезает на дешёвую VPS, без Docker в продакшене

## Архитектура (обзор)

Каждое событие в системе — RFC 5322 письмо (`.eml`-файл). Стадии FSM — папки Maildir. Переход между стадиями — доставка письма через `fdm` + `notmuch insert`. Оркестрация — `systemd --user` (воркеры, перезапуски, лимиты ресурсов, логи через journalctl).

### Стадии FSM

`ingress` → `enrich` → `reasoning` → (`egress_router` | `cli_intent` | `thread_memory` | `global_memory` | `subagent_intent` | `reflect`) → ... → `egress_email` / `egress_telegram` / `egress_matrix` → `archive`

Каждая стадия — Python-модуль с одной функцией:

```python
def main(msg: EmailMessage, stage: FsmStage, *, config: Config) -> EmailMessage | None:
```

### Компоненты

| Компонент          | Реализация                                    |
| ------------------ | --------------------------------------------- |
| Хранилище событий  | Maildir (файлы на диске)                      |
| Индекс             | notmuch (Xapian)                              |
| Очередь            | Maildir `new/` → `cur/`                       |
| Оркестрация        | systemd --user                                |
| Маршрутизация      | fdm (`~/.fdm.conf`)                           |
| Рассуждение        | litellm + tool calls                          |
| Память             | LightRAG (NanoVectorDB + NetworkX)            |
| Каналы             | IMAP IDLE, Telegram Bot API, Matrix (nio)     |
| Промпты            | Jinja2 шаблоны                                |
| Развёртывание      | Ansible push-модель                           |
| Конфигурация       | pydantic-settings + YAML (`threlium.yaml`)    |
| Тестирование       | pytest e2e + Docker + WireMock + GreenMail    |
| Безопасность CLI   | cli_intent (политика) → cli_exec (песочница)  |

Подробное описание архитектуры со схемами: [docs/ARTICLE.md](docs/ARTICLE.md)

## Требования

- **Целевой хост:** Ubuntu 24.04+ (Debian-based) с systemd
- **Python:** 3.11+
- **LLM:** OpenAI-совместимый endpoint (локальный vLLM, ollama или облачный)
- **Embedding:** OpenAI-совместимый endpoint для эмбеддингов (LightRAG)
- **IMAP-сервер:** для email-канала (GreenMail для тестов, любой реальный для прода)
- **Control node:** Ansible 2.20+ (для деплоя)

## Установка

### 1. Клонировать репозиторий (на control node)

```bash
git clone <repo-url> threlium
cd threlium
```

### 2. Настроить inventory

Скопировать и отредактировать файл инвентаря:

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

### 3. Настроить host_vars

Создать файл переменных хоста (пример: `ansible/host_vars/th-agent.yml`). Минимально необходимо:

```yaml
# Пароль для PAM-авторизации (Cockpit, Roundcube)
threlium_agent_login_password: "your-password"

# LLM endpoints (OpenAI-совместимые)
threlium_litellm:
  llm_endpoints:
    - model: "openai/qwen3-35b"
      api_base: "http://your-vllm-host:8000/v1"
      score: 1.0
      chat_template_kwargs:
        enable_thinking: true
  embedding_endpoints:
    - model: "openai/bge-m3"
      api_base: "http://your-vllm-host:8001/v1"

# Email-мост (IMAP)
threlium_bridges:
  email:
    imap_host: "imap.example.com"
    imap_user: "agent@example.com"
    imap_pass: "app-password"

# SMTP для отправки ответов
threlium_msmtp:
  host: "smtp.example.com"
  port: 587
  user: "agent@example.com"
  password: "app-password"
```

### 4. Запустить деплой

```bash
ansible-playbook ansible/playbooks/site.yml \
  -i ansible/inventory/my-host.yml \
  -e @ansible/host_vars/my-host.yml \
  --tags deploy
```

Плейбук установит все зависимости (fdm, msmtp, notmuch, python3, Cockpit, Caddy, Roundcube, Dovecot), создаст пользователя, развернёт код, промпты, конфиги, systemd-юниты и запустит агента.

### 5. Обновление кода (без полного деплоя)

```bash
ansible-playbook ansible/playbooks/site.yml \
  -i ansible/inventory/my-host.yml \
  --tags refresh
```

Режим `refresh` синхронизирует код и конфиги без apt/pip/веб-стека.

## Использование

После деплоя агент запущен и слушает входящие сообщения. Взаимодействие:

- **Email:** отправить письмо на адрес агента — ответ придёт в тот же тред
- **Telegram:** написать боту (если настроен `threlium_bridge_telegram_enabled`)
- **Matrix:** написать в комнату (если настроен `threlium_bridge_matrix_enabled`)

### Веб-админка

После деплоя доступна на порту `:8080` целевого хоста:

- `/webmail/` — Roundcube (read-only просмотр всех «мыслей» агента как почтовых тредов)
- `/` — Cockpit (терминал, journald-логи, управление systemd-юнитами, метрики)

### Управление сервисами

```bash
# На целевом хосте под пользователем threlium:
systemctl --user status threlium-engine.service
systemctl --user restart threlium-engine.service
journalctl --user -u threlium-engine.service -f
```

## Тестирование

E2e-тесты запускают полный контур в Docker (Ubuntu 24.04 SUT + GreenMail + WireMock):

```bash
pip install -e ".[e2e]"
pytest tests/e2e/
```

Стратегия baked-образа: первый прогон выполняет полный `site.yml` на голом Ubuntu и коммитит образ. Последующие тесты стартуют из baked-образа мгновенно.

## Структура проекта

```
ansible/
  playbooks/site.yml              # единственный сценарий деплоя
  playbooks/tasks/                # вложенные задачи (refresh, web-стек, ssh)
  roles/threlium/
    defaults/main.yml             # дефолтные переменные
    vars/main.yml                 # канон FSM-стадий
    files/scripts/                # Python-код FSM (пакет threlium)
    files/prompts/                # Jinja2-промпты для LLM
    templates/                    # шаблоны конфигов, systemd-юнитов
  host_vars/                      # per-host переменные (LLM endpoints, секреты)
  inventory/                      # инвентари (прод и e2e)
tests/e2e/                        # e2e-тесты (Docker + WireMock + GreenMail)
docs/                             # документация и статья об архитектуре
```

## Документация

- [docs/ARTICLE.md](docs/ARTICLE.md) — подробная статья об архитектуре со схемами
- [docs/TYPES.md](docs/TYPES.md) — описание типов данных
