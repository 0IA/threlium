# Agent navigation under partial information (Threlium)

> **Threlium:** This is the guide for navigating a task when the context you see is
> incomplete. Retrieve it via `memory_query` when a request needs several hops, when
> you are unsure whether to retrieve / verify / answer, or when a draft rests on an
> unchecked assumption. For the full route map see `fsm_routes.md`; for the durable
> plan mechanics see `agent_task_ledger.md`.

You move through a task like someone walking a labyrinth toward a goal: at each step you
see only the current turn, you remember some turns already taken, and you must form
expectations about what lies ahead to choose well — but you MUST verify an expectation
before you commit far on it, so imagination does not diverge from reality. The enrich
sample, the thread history, and the task ledger are your lit corridor, your trail, and
your map; they are never the whole maze.

This is the operational meaning of the FSM loop `ingress → enrich → reasoning → route →
… → egress`: each turn you read what is currently visible, pick exactly one route, and
read the observation it returns before the next decision.

## What you can see vs. what exists

- The context blocks `<knowledge_graph>`, `<thread_memory>`, `<global_memory>`,
  `<conversation_history>` are ONE enrich retrieval sample for this turn — the lit
  corridor. Absence from them does NOT mean absence from the graph or the project.
- `<conversation_delta>` and `<observation>` are your trail: turns and tool results
  already seen this thread.
- The user request plus `<task_state>` (the durable ledger) is your map of the goal.

## Routes as moves in the maze

| Move | Question it answers | Route |
|------|---------------------|-------|
| Look down one corridor (explore) | "What is around this entity / where is X handled?" | `memory_query` (graph), `cli_intent` (files), `subagent_intent` (broad survey) |
| Redraw the whole map (remap) | "I need the context rebuilt around several entities." | `reflect` — only after at least one targeted `memory_query`/`cli_intent` |
| Test that a corridor is not a dead end (prove) | "Is this claim / invariant actually true?" | `formal_reason` (RDF/SPARQL on a graph you author) |
| Stop at a junction and check the map (checkpoint) | "Does my draft match the plan and the evidence?" | `response_observe` before `response_edit` / `response_finalize` |
| Persist the trail | "Remember this fact for later." | `thread_memory` (this thread) / `global_memory` (cross-thread) |
| Leave the maze (exit) | "The goal is reached and verified." | `response_finalize` with `verification_summary`, gated on the ledger |

Discovery order (same as `fsm_routes.md`): in-context → `memory_query` → `cli_intent`
(files) → `subagent_intent` (broad survey) → `reflect` (graph refresh). Do not skip to a
long answer or to new code before checking what already exists.

## Phases of work: discover → verify → deliver

A plan typically moves through three phases (not every request needs all three):

- **discover** — find a fact, a relation, or where something lives in the project/graph.
- **verify** — confirm a relation, an invariant, or a multi-step conclusion before you
  assert it.
- **deliver** — produce the answer to the user or the artifact.

You express a subtask's phase through its **verb**, never through a literal tag in the
text: discover → Find / Locate / Identify / Survey; verify → Confirm / Verify / Check /
Prove; deliver → Draft / Write / Answer / Compose. A trivial request is a single deliver
subtask, e.g. `Answer the user's question about X`.

Why no bracketed tags like `[verify]`: a subtask's `content_id` is a hash of its
normalized text (see `agent_task_ledger.md`). Adding or changing a tag changes the
`content_id`, which would create a duplicate subtask on the next enrich seed and could
block `response_finalize`. The phase is read from the verb by the reasoning and
`response_observe` stages; nothing parses the text mechanically, so a literal tag buys
no machine function and only risks the ledger. Keep existing subtask wording verbatim.

## Verify before you commit far

The core discipline: do not let an unchecked expectation carry the answer.

- A missing FACT → `memory_query` (targeted) or `reflect` (broad). Never fill the gap
  from your own training knowledge while the graph still has an unqueried path.
- An unverified CONCLUSION you already have the facts for → `formal_reason` before you
  assert it as fact in the reply.
- A long or structured reply → `response_observe` to separate what is **explored**
  (backed by graph / observation / cli) from what is still **assumed**, then resolve the
  assumed parts before `response_finalize`.

## Anti-patterns

- **reflect without exploring first** — `reflect` is a remap, not a file search and not a
  proof. Run a targeted `memory_query` (and `cli_intent` for code) before it.
- **reflect again with no new map** — if a fresh enrich added nothing about the entities
  you named in `clarification_request`, do not reflect again; `memory_query` by entity
  name or finalize stating the gap.
- **finalize with an open verify subtask** — if a Confirm/Verify subtask is still open
  and the buffer does not cover it, do not finalize; the ledger gate will refuse anyway.
- **vacuous formal_reason** — `conforms=true` with zero matched target nodes is not a
  proof; confirm coverage (see `shacl_sparql.md`).
- **answering from priors** — for project facts, prefer the graph (`memory_query`) over
  internal knowledge; state the gap explicitly when retrieval returns nothing.
