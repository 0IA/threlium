"""E2E тест стадии memory_query → response_finalize.

Сценарий: reasoning → memory_query (retrieve marker) → enrich_fast → reasoning →
response_finalize.

Покрытие:
- memory_query handler: aquery к LightRAG, observation relay через enrich_fast
- Доказательство сквозного data flow: query-маркер доходит до embedding API
- enrich_fast: relay OBSERVATION_NOTE между reasoning хопами

Стабы используют фазовый автомат WireMock State Extension:
- phase_query_done: после первого reasoning → memory_query
- Второй reasoning видит phase_query_done → response_finalize
"""
from __future__ import annotations

from pathlib import Path

from tests.e2e.log import clip_log_body, log

from .toolkit import (
    E2EComposeRuntime,
    E2EComposeRuntime,
    MailflowScenarioSpec,
    assert_full_mailflow_pipeline,
    discover_runtime,
    dump_failure_artifacts,
    mailflow_inject_and_wait,
    REPO_ROOT,
)
from .wiremock_client import (
    wiremock_public_base,
    wiremock_state_thread_root_list_size,
)

_WIREMOCK_STUBS_ROOT = Path(__file__).resolve().parent / "wiremock_stubs"
E2E_MEMORY_QUERY_BODY_MARKER = "E2E-MEMORY-QUERY-BODY"
E2E_MEMORY_QUERY_MARKER = "E2E-MEMORY-QUERY-MARKER"

MEMORY_QUERY_SPEC = MailflowScenarioSpec(
    label="memory_query",
    raw_id_prefix="e2e-mem-query-",
    stub_dir=_WIREMOCK_STUBS_ROOT / "test_memory_query_e2e",
    stub_tag="stub-memory-query-01",
    body_head=f"{E2E_MEMORY_QUERY_BODY_MARKER}\ne2e memory query verification test body",
    min_chat_completion_posts=3,
    min_embedding_posts=1,
    min_rerank_posts=0,
    reply_body_needle="e2e-memory-query-verified-answer",
    # Detag (§3.6.8): scripted reasoning via the SHARED generic reasoning stub-set (no per-test
    # reasoning JSON). Phase 0 memory_query (carries the marker → embed → state flag), phase 1
    # tasks_upsert, phase 2 response_finalize.
    reasoning_phases=[
        (
            "memory_query",
            {
                "reasoning": "e2e: retrieving domain knowledge from the graph",
                "query": "E2E-MEMORY-QUERY-MARKER SHACL sh:sparql constraint",
            },
        ),
        (
            "tasks_upsert",
            {
                "reasoning": "e2e: record task completion before finalize",
                "new_subtasks": [{"text": "Complete the user request", "status": "done"}],
            },
        ),
        (
            "response_finalize",
            {
                "reasoning": "e2e: finalizing after memory query verification",
                "subject": "Re: e2e reply",
                "verification_summary": "e2e: knowledge retrieved and verified via memory_query",
                "content": "e2e-memory-query-verified-answer",
            },
        ),
    ],
)


def _assert_embedding_contains_query_marker(project: str) -> None:
    """Verify that at least one embedding request carried the query marker text.

    Proves the data round-trip: stub tool_call -> handler parse -> rag.aquery(payload.query) ->
    embedding API with the expected query text. Read from WireMock **state**, not the journal: the
    embeddings stub APPEND-ONLY records a ``hit`` into the fixed context ``saw-memory-query-marker``
    whenever an embed body contains ``E2E-MEMORY-QUERY-MARKER`` (the marker is globally unique to this
    single-instance test, so a fixed context is collision-free; append-only is concurrency-safe — no
    read-modify-write). Journal-independent (docs/E2E.md §3.6.7); the marker bypasses the unreliable
    thread-root header on lightrag embeds (§3.6.3).
    """
    rt = discover_runtime(project, repo_root=REPO_ROOT)
    wm_base = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
    hits = wiremock_state_thread_root_list_size(wm_base, "saw-memory-query-marker")
    assert hits >= 1, (
        f"No embedding request carried query marker {E2E_MEMORY_QUERY_MARKER!r} "
        f"(state list_size(saw-memory-query-marker)={hits}). "
        "This means memory_query did not pass the expected query text to LightRAG."
    )
    log.info("roundtrip_embedding_marker_verified", marker=E2E_MEMORY_QUERY_MARKER, hits=hits)


def test_memory_query_full_pipeline(e2e_runtime: E2EComposeRuntime) -> None:
    """Memory system: memory_query(retrieve) -> response_finalize."""
    with mailflow_inject_and_wait(MEMORY_QUERY_SPEC, e2e_runtime.project_name) as (
        project,
        raw_id,
        _canonical_id,
        nm_inner,
        stub_tag,
        correlation_key,
    ):
        try:
            assert_full_mailflow_pipeline(
                MEMORY_QUERY_SPEC,
                project=project,
                raw_id=raw_id,
                nm_inner=nm_inner,
                stub_tag=stub_tag,
                correlation_key=correlation_key,
            )
            _assert_embedding_contains_query_marker(project)
        except Exception:
            log.debug(
                "failure_artifacts",
                body=clip_log_body(dump_failure_artifacts(project, repo_root=REPO_ROOT)),
            )
            raise
