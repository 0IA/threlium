# Channel bridges — shared contract

Threlium normalizes external channels (email, matrix, telegram) into canonical RFC messages delivered to `ingress@localhost` via `fdm` + `notmuch insert`. All bridges live under `threlium/bridges/` and are registered in `bridges/registry.py` (`BRIDGE_RUNNERS`).

## Layout

| Module | Channel | Role |
|--------|---------|------|
| `bridges/email.py` | email | IMAP IDLE → canonicalize → `run_fdm` |
| `bridges/matrix.py` | matrix | Matrix sync → canonicalize → `run_fdm` |
| `bridges/telegram.py` | telegram | Bot updates → canonicalize → `run_fdm` |
| `bridges/registry.py` | — | Maps `BridgeIngressChannel` → `run_bridge` handler |

Runner entry: `python -m threlium.runners.bridge <channel>` (`threlium/runners/bridge.py`).

## Contract rules

- **Single delivery path**: bridges call `run_fdm(fdm_bytes_from_message(msg))` — never write directly to `stages/ingress/Maildir/new/`.
- **Canonical headers**: `From: <channel>@localhost`, `To: ingress@localhost`, `X-Threlium-Route` (b62 JSON ingress route), wire `Message-ID` / `In-Reply-To` for notmuch graph.
- **No parallel implementations**: when extending one channel (e.g. email), read `matrix.py` and `telegram.py` for the same patterns (canonicalize, dedup, watermark, deliver callback).
- **Shared helpers**: `bridges/notmuch_space_anchor.py` and types in `threlium/types/bridges.py` — reuse rather than duplicating notmuch/route logic in each bridge.

## Anti-duplication for agents

Before adding a new bridge module or duplicating ingress logic elsewhere, search `threlium/bridges/` and extend the existing channel file or shared registry. Policy and FSM stages (`ingress`, `enrich`, `reasoning`) are separate from bridge code — do not reimplement routing inside a bridge.
