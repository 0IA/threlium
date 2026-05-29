# FSM routes available from reasoning

The `reasoning@localhost` stage chooses exactly one tool call per turn. Each tool maps to a mailbox stage (`ROUTE_TO_ADDRESS` in `states/reasoning.py`).

## Knowledge and memory (same LightRAG graph)

| Route | Stage chain | Use |
|-------|-------------|-----|
| `memory_query` | `memory_query@` → `enrich_fast@` → `reasoning@` | Targeted graph lookup: facts, relations, docs (`knowledge/*.md`), past notes. Cheap. Use when context blocks lack a fact but it may exist in the graph. |
| `reflect` | `reflect@` → `ingress@` → `enrich@` → `reasoning@` | Broad re-enrich with new formulation. Use when several targeted `memory_query` calls cannot connect entities. |
| `thread_memory` | `thread_memory@` → `ingress@` → … | Store a fact for this dialog thread (indexed into graph). |
| `global_memory` | `global_memory@` → `ingress@` → … | Store a cross-thread fact (indexed into graph). |

Context blocks `<knowledge_graph>`, `<thread_memory>`, `<global_memory>` are one enrich sample — absence there does not mean absence in the graph.

## Discovery and execution

| Route | Stage chain | Use |
|-------|-------------|-----|
| `cli_intent` | `cli_intent@` → `cli_exec@` / HITL / deny → `ingress@` | Shell on agent host: read-only discovery (`rg`, `find`, `cat`, `head`, `git grep`/`log`/`show`) or implementation commands. One argv array per call. |
| `subagent_intent` | subagent frame → `subagent_end` | Isolated multi-step work; use inventory-only tasks to survey repo without polluting parent thread. |

Discovery order: in-context → `memory_query` → `cli_intent` (files) → `subagent_intent` (broad survey) → `reflect` (graph refresh). Do not skip to new code without checking existing implementations.

## Response buffer

| Route | Notes |
|-------|--------|
| `response_append` / `response_edit` / `response_observe` | Build long replies incrementally; `enrich_fast` relays buffer state. |
| `response_finalize` | Required to deliver reply; never call `egress_router` directly from reasoning. |

## Verification

| Route | Use |
|-------|-----|
| `logic_validate` | SHACL/pySHACL proof of derivations — not for fetching facts (`memory_query`). |

## Related bootstrap docs

- `knowledge/turtle_syntax.md`, `shacl_sparql.md`, `sparql_functions.md` — SHACL/Turtle reference for `logic_validate`.
