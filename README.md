# Threlium

**English** | [Русский](README.ru.md)

A self-hosted AI agent built from Unix primitives: Maildir, systemd, **fdm**, notmuch. Communicates via Email (IMAP IDLE), Telegram, and Matrix. Multi-step reasoning via LLM tool calls; long-term memory in LightRAG; shell execution and self-modification through a gated CLI pipeline.

## Features

- **~11k lines of Python (SLOC)** — FSM handlers and runners (`threlium/`, without `types/`); configs, prompts, and Ansible instead of frameworks
- **FSM on Maildirs** — each event is an RFC 5322 message; each stage is `stages/<stage>/Maildir/`
- **Union notmuch index** — one database over all stage maildirs; durable history in `cur/`, no separate legacy archive tree
- **Orchestration via systemd --user** — `fdm` → `notmuch insert` → `threlium-dispatch.sh` → `threlium-work@` / `threlium-engine`
- **Three I/O channels** — symmetric `threlium.bridges.*` → canonical `ingress@localhost`
- **Three-layer memory** — thread context, global facts, LightRAG knowledge graph (RAG-loop inside `threlium-engine`)
- **CLI with security policy** — `cli_intent` (policy) → `cli_exec` (sandbox) + HITL
- **Subagents & formal reasoning** — IRT chains, hop budget, SHACL/SPARQL gate (`formal_reason`)
- **Web admin** — Cockpit + Roundcube + Dovecot (agent traffic visible as mail threads)
- **Self-modification** — agent may commit changes in local `threlium_repo_path` via privileged `cli_exec`
- **Minimal production footprint** — bare metal or VPS; Docker only for e2e harness

## Architecture (overview)

External signals are normalized once by channel bridges into canonical MIME (`To: ingress@localhost`, `X-Threlium-Route`). The FSM engine (`threlium.runners.engine`) calls stage handlers **in-process**; transitions deliver the next message via **`run_fdm`** → terminating **`fdm` pipe** → `notmuch insert` + dispatch. LightRAG indexing runs on a dedicated asyncio loop in the same daemon after `nm_settle()`.

Typical happy path:

`ingress` → `enrich` → `reasoning` → (`egress_router` | memory | CLI | subagent | `formal_reason` | response tools | …) → `egress_<channel>` → `archive`

Stage contract:

```python
def main(msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings) -> EmailMessage | None:
```

| Layer | Implementation |
| ----- | -------------- |
| Event store | Durable stage Maildirs under `~/threlium/stages/` |
| Index | notmuch2 (union over `stages/*/Maildir`) |
| Routing | fdm (`~/.fdm.conf`) |
| Orchestration | systemd --user (`threlium-engine`, `threlium-work@`, bridges, sweep) |
| Reasoning | litellm + tool calls (edge choice is never free-text parsing) |
| Memory | LightRAG + `thread_memory` / `global_memory` |
| Wire MIME | `threlium.mail` (parse/serialize/IMAP); domain types in `threlium.types` (msgspec) |
| Deployment | Ansible push (`site.yml`: `deploy` / `refresh`) |
| Testing | pytest e2e only — Docker Compose + baked SUT + WireMock + GreenMail |

Normative detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/INDEX.md](docs/INDEX.md), [docs/FSM.md](docs/FSM.md). Narrative article (Russian): [docs/ARTICLE.md](docs/ARTICLE.md).

## Requirements

- **Target host:** Ubuntu 24.04+ (Debian-based), systemd, `loginctl enable-linger` for the agent user
- **Python:** 3.11+ (runtime venv on target; repo root `.venv` for dev/e2e)
- **LLM / embeddings:** OpenAI-compatible HTTP (vLLM, Ollama, cloud, …)
- **IMAP/SMTP:** for the email channel (GreenMail in e2e)
- **Control node:** Ansible 2.20+ for deployment

System packages on target include **fdm**, **notmuch**, **msmtp**, **dovecot**, **cockpit**, **caddy** (see role defaults).

## Installation

### 1. Clone (control node)

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

### 3. Host variables

Create `ansible/host_vars/my-server.yml`. Minimum:

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

Full variable map: [docs/PLAYBOOK.md](docs/PLAYBOOK.md).

### 4. Deploy

```bash
ansible-playbook ansible/playbooks/site.yml \
  -i ansible/inventory/my-host.yml \
  -e @ansible/host_vars/my-server.yml \
  --tags deploy
```

### 5. Code refresh (no apt/web reinstall)

```bash
ansible-playbook ansible/playbooks/site.yml \
  -i ansible/inventory/my-host.yml \
  --tags refresh
```

Re-running full `deploy` on a live host is **disaster recovery** (overwrites local git drift in `threlium_repo_path`). Day-to-day code changes on target go through local commits or `refresh` from the control node.

## Usage

- **Email** — send to the agent address; replies stay in the same thread (`References` / `In-Reply-To`)
- **Telegram / Matrix** — enable in `threlium_bridges` and systemd bridge units

**Web UI** (after deploy): `https://<host>:9090` (Cockpit), `http://<host>:8080/webmail/` (Roundcube).

```bash
# On target as the threlium user:
systemctl --user status threlium-engine.service
journalctl --user -u threlium-engine.service -f
```

## Testing

E2e is the only automated test layer ([docs/TESTING.md](docs/TESTING.md)):

```bash
.venv/bin/pip install -e ".[e2e]"

# First time or after playbook/package changes — bake SUT image:
.venv/bin/pytest -n0 tests/e2e/wipe_bake.py

# Scenarios (shared Docker Compose: sut + greenmail + wiremock):
.venv/bin/pytest tests/e2e

# Serial runner with per-test logs:
./test-runs/run_individual_e2e.sh
```

After RFC822 / `threlium.mail` changes: `scripts/check_mail_wire.sh`.

## Project layout

```
ansible/
  playbooks/site.yml           # deploy + refresh
  roles/threlium/
    files/scripts/threlium/    # FSM, bridges, runners, types, mail/
    files/prompts/             # Jinja2 LLM prompts
    files/knowledge/           # bootstrap corpus for LightRAG
    templates/                 # threlium.yaml, fdm.conf, systemd units
tests/e2e/                     # pytest + compose + wiremock_stubs/
docs/                          # architecture contracts (mostly Russian)
```

## Documentation

| Document | Topic |
| -------- | ----- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System overview, integrations |
| [docs/INDEX.md](docs/INDEX.md) | Storage + notmuch + LightRAG contract |
| [docs/FSM.md](docs/FSM.md) | Stage graph and handler contract |
| [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md) | systemd, dispatch, concurrency |
| [docs/PLAYBOOK.md](docs/PLAYBOOK.md) | Ansible deployment |
| [docs/TESTING.md](docs/TESTING.md) | E2e harness |
| [docs/TYPES.md](docs/TYPES.md) | msgspec / wire types |
| [docs/ARTICLE.md](docs/ARTICLE.md) | Long-form architecture article (RU) |
