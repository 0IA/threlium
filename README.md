# Threlium

**English** | [Русский](README.ru.md)

A self-hosted AI agent built from Unix primitives: Maildir, systemd, fdm, notmuch. Communicates via Email (IMAP), Telegram, and Matrix. Performs multi-step reasoning using LLM tool calls, maintains long-term memory in a knowledge graph (LightRAG), can execute shell commands and modify its own code.

Detailed architecture article (in Russian): [docs/ARTICLE.md](docs/ARTICLE.md)

## Features

- **~6k lines of Python** — minimal code, configs and scripts instead of frameworks
- **FSM on Maildirs** — every event = RFC 5322 message, every stage = folder on disk
- **Orchestration via systemd --user** — no Celery, RabbitMQ, Kubernetes
- **Three I/O channels** — Email (IMAP IDLE), Telegram (Bot API), Matrix (nio)
- **Three-layer memory** — thread context, global facts, LightRAG (knowledge graph)
- **CLI with security policy** — decision/policy/execution separation + HITL
- **Subagents** — recursive calls via IRT chains
- **Web admin panel** — Cockpit + Roundcube + Dovecot (agent's thoughts visible as mail threads)
- **Self-modification** — the agent can edit its own prompts, configs, and code
- **Minimal resources** — fits on a cheap VPS, no Docker in production

## Architecture (overview)

Every event in the system is an RFC 5322 message (`.eml` file). FSM stages are Maildir folders. Transitions between stages are message deliveries via `fdm` + `notmuch insert`. Orchestration is handled by `systemd --user` (workers, restarts, resource limits, logs via journalctl).

### FSM stages

`ingress` → `enrich` → `reasoning` → (`egress_router` | `cli_intent` | `thread_memory` | `global_memory` | `subagent_intent` | `reflect`) → ... → `egress_email` / `egress_telegram` / `egress_matrix` → `archive`

Each stage is a Python module with a single function:

```python
def main(msg: EmailMessage, stage: FsmStage, *, config: Config) -> EmailMessage | None:
```

### Components

| Component          | Implementation                                |
| ------------------ | --------------------------------------------- |
| Event store        | Maildir (files on disk)                       |
| Index              | notmuch (Xapian)                              |
| Queue              | Maildir `new/` → `cur/`                       |
| Orchestration      | systemd --user                                |
| Routing            | fdm (`~/.fdm.conf`)                           |
| Reasoning          | litellm + tool calls                          |
| Memory             | LightRAG (NanoVectorDB + NetworkX)            |
| Channels           | IMAP IDLE, Telegram Bot API, Matrix (nio)     |
| Prompts            | Jinja2 templates                              |
| Deployment         | Ansible push model                            |
| Configuration      | pydantic-settings + YAML (`threlium.yaml`)    |
| Testing            | pytest e2e + Docker + WireMock + GreenMail    |
| CLI security       | cli_intent (policy) → cli_exec (sandbox)      |

Detailed architecture description with diagrams: [docs/ARTICLE.md](docs/ARTICLE.md)

## Requirements

- **Target host:** Ubuntu 24.04+ (Debian-based) with systemd
- **Python:** 3.11+
- **LLM:** OpenAI-compatible endpoint (local vLLM, ollama, or cloud)
- **Embedding:** OpenAI-compatible endpoint for embeddings (LightRAG)
- **IMAP server:** for the email channel (GreenMail for tests, any real server for production)
- **Control node:** Ansible 2.20+ (for deployment)

## Installation

### 1. Clone the repository (on the control node)

```bash
git clone <repo-url> threlium
cd threlium
```

### 2. Configure inventory

Copy and edit the inventory file:

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

### 3. Configure host_vars

Create a host variables file (example: `ansible/host_vars/th-agent.yml`). Minimum required:

```yaml
# Password for PAM auth (Cockpit, Roundcube)
threlium_agent_login_password: "your-password"

# LLM endpoints (OpenAI-compatible)
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

# Email bridge (IMAP)
threlium_bridges:
  email:
    imap_host: "imap.example.com"
    imap_user: "agent@example.com"
    imap_pass: "app-password"

# SMTP for sending replies
threlium_msmtp:
  host: "smtp.example.com"
  port: 587
  user: "agent@example.com"
  password: "app-password"
```

### 4. Run the deployment

```bash
ansible-playbook ansible/playbooks/site.yml \
  -i ansible/inventory/my-host.yml \
  -e @ansible/host_vars/my-host.yml \
  --tags deploy
```

The playbook will install all dependencies (fdm, msmtp, notmuch, python3, Cockpit, Caddy, Roundcube, Dovecot), create a user, deploy code, prompts, configs, systemd units, and start the agent.

### 5. Code update (without full deployment)

```bash
ansible-playbook ansible/playbooks/site.yml \
  -i ansible/inventory/my-host.yml \
  --tags refresh
```

The `refresh` mode syncs code and configs without apt/pip/web stack.

## Usage

After deployment, the agent is running and listening for incoming messages. Interaction:

- **Email:** send a message to the agent's address — the reply will arrive in the same thread
- **Telegram:** message the bot (if `threlium_bridge_telegram_enabled` is configured)
- **Matrix:** message the room (if `threlium_bridge_matrix_enabled` is configured)

### Web admin panel

Available on port `:8080` of the target host after deployment:

- `/webmail/` — Roundcube (read-only view of all agent "thoughts" as mail threads)
- `/` — Cockpit (terminal, journald logs, systemd unit management, metrics)

### Service management

```bash
# On the target host as the threlium user:
systemctl --user status threlium-engine.service
systemctl --user restart threlium-engine.service
journalctl --user -u threlium-engine.service -f
```

## Testing

E2e tests run the full pipeline in Docker (Ubuntu 24.04 SUT + GreenMail + WireMock):

```bash
pip install -e ".[e2e]"
pytest tests/e2e/
```

Baked image strategy: the first run executes the full `site.yml` on bare Ubuntu and commits the image. Subsequent tests start instantly from the baked image.

## Project structure

```
ansible/
  playbooks/site.yml              # single deployment scenario
  playbooks/tasks/                # included tasks (refresh, web stack, ssh)
  roles/threlium/
    defaults/main.yml             # default variables
    vars/main.yml                 # canonical FSM stages
    files/scripts/                # Python FSM code (threlium package)
    files/prompts/                # Jinja2 prompts for the LLM
    templates/                    # config and systemd unit templates
  host_vars/                      # per-host variables (LLM endpoints, secrets)
  inventory/                      # inventories (prod and e2e)
tests/e2e/                        # e2e tests (Docker + WireMock + GreenMail)
docs/                             # documentation and architecture article
```

## Documentation

- [docs/ARTICLE.md](docs/ARTICLE.md) — detailed architecture article with diagrams (in Russian)
- [docs/TYPES.md](docs/TYPES.md) — data type descriptions
