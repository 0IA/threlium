"""E2E: formal_reason with a SPARQL query (no inference) → finalize.

Сценарий: reasoning → formal_reason (conforms=true, query=SELECT) →
enrich_fast → reasoning → response_finalize.

Покрытие (detag §3.6.2/§3.6.8 — generic reasoning + STATE-флаги, без journal-скана):
- formal_reason возвращает query_result (SPARQL bindings) в observation-note → reasoning видит
  ``query_result:`` (content-flag ``saw_query_result``)
- гейт formal_reason НЕ активируется (query без нарушений) → ``saw_gate_active == 0``
- min 3 chat completion (formal_reason + tasks_upsert + finalize)
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
from .wiremock_client import wiremock_public_base, wiremock_state_thread_root_list_size

_WIREMOCK_STUBS_ROOT = Path(__file__).resolve().parent / "wiremock_stubs"
E2E_FORMAL_REASON_QUERY_BODY = "E2E-FORMAL-REASON-QUERY-BODY"

# Detag (§3.6.8): generic reasoning, линейный цикл formal_reason → tasks_upsert → response_finalize.
# Гейт formal_reason НЕ срабатывает (query без нарушений), поэтому фазы линейны.
_QUERY_PHASES = [
    (
        "formal_reason",
        {
            "reasoning": "e2e: SPARQL SELECT over conforming facts — read query_result",
            "query": "PREFIX ex: <http://example.org/>\nSELECT ?name WHERE { ?p ex:name ?name } ORDER BY ?name",
            "shapes_ttl": (
                "@prefix sh: <http://www.w3.org/ns/shacl#> .\n@prefix ex: <http://example.org/> .\n\n"
                "ex:PersonShape a sh:NodeShape ;\n  sh:targetClass ex:Person ;\n"
                "  sh:property [ sh:path ex:name ; sh:minCount 1 ] ."
            ),
            "facts_ttl": (
                '@prefix ex: <http://example.org/> .\n\nex:alice a ex:Person ;\n  ex:name "Alice" .\n\n'
                'ex:bob a ex:Person ;\n  ex:name "Bob" .\n'
            ),
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
            "reasoning": "e2e: finalizing after formal_reason SPARQL query",
            "subject": "Re: e2e reply",
            "verification_summary": "e2e: formal_reason returned query_result bindings for ex:name",
            "content": "e2e-formal-reason-query-verified-answer",
        },
    ),
]

FORMAL_REASON_QUERY_SPEC = MailflowScenarioSpec(
    label="formal_reason_query",
    raw_id_prefix="e2e-formal-reason-query-",
    stub_dir=_WIREMOCK_STUBS_ROOT / "test_formal_reason_query_e2e",
    stub_tag="stub-formal-reason-query-01",
    body_head=(
        f"{E2E_FORMAL_REASON_QUERY_BODY}\n"
        "e2e formal_reason sparql query result test body"
    ),
    min_chat_completion_posts=3,
    min_embedding_posts=1,
    min_rerank_posts=0,
    reply_body_needle="e2e-formal-reason-query-verified-answer",
    reasoning_phases=_QUERY_PHASES,
)


def test_formal_reason_query_full_pipeline(e2e_runtime: E2EComposeRuntime) -> None:
    with mailflow_inject_and_wait(FORMAL_REASON_QUERY_SPEC, e2e_runtime.project_name) as (
        project,
        raw_id,
        _canonical_id,
        nm_inner,
        stub_tag,
        correlation_key,
    ):
        try:
            assert_full_mailflow_pipeline(
                FORMAL_REASON_QUERY_SPEC,
                project=project,
                raw_id=raw_id,
                nm_inner=nm_inner,
                stub_tag=stub_tag,
                correlation_key=correlation_key,
            )
            rt = discover_runtime(project, repo_root=REPO_ROOT)
            wm_base = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
            # Detag (§3.6.8): additive presence. Гейт formal_reason не активировался (нет 'Gate retry
            # counter:' ни в одном reasoning-теле ⇒ size 0), а query_result дошёл (size>=1).
            assert (
                wiremock_state_thread_root_list_size(wm_base, f"saw-gate-active-{correlation_key}") == 0
            ), "formal_reason gate must NOT activate for a conforming SPARQL query (state saw_gate_active)"
            assert (
                wiremock_state_thread_root_list_size(wm_base, f"saw-query-result-{correlation_key}") >= 1
            ), "formal_reason query_result bindings must reach reasoning (state saw_query_result)"
        except Exception:
            log.debug(
                "failure_artifacts",
                body=clip_log_body(dump_failure_artifacts(project, repo_root=REPO_ROOT)),
            )
            raise
