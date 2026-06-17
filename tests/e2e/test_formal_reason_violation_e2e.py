"""E2E: formal_reason with SHACL violation (conforms=false) → enrich_fast → reasoning → finalize.

Сценарий: reasoning → formal_reason (invalid facts, ex:age -1) → enrich_fast →
reasoning (observation с conforms: False) → response_finalize.

Покрытие:
- formal_reason: conforms=False, violations > 0
- observation-note relay через enrich_fast
- фазовый WireMock State: phase_logic_done

Стабы: ``wiremock_stubs/test_formal_reason_violation_e2e/`` (``stub-formal-reason-violation-01``).
"""
from __future__ import annotations

from pathlib import Path


from tests.e2e.log import clip_log_body, log

from .toolkit import (
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
E2E_FORMAL_REASON_VIOLATION_BODY = "E2E-FORMAL-REASON-VIOLATION-BODY"

# Detag (§3.6.8): generic reasoning, линейный formal_reason → tasks_upsert → response_finalize.
# Гейт НЕ срабатывает (нарушение SHACL само по себе гейт не активирует), результат conforms:False/
# violations: доходит до reasoning (content-flags на generic 200-207).
_VIOLATION_PHASES = [
    (
        "formal_reason",
        {
            "reasoning": "e2e: SHACL check with intentionally invalid age",
            "shapes_ttl": (
                "@prefix sh: <http://www.w3.org/ns/shacl#> .\n@prefix ex: <http://example.org/> .\n\n"
                "ex:PositiveAgeShape a sh:NodeShape ;\n  sh:targetClass ex:Person ;\n  sh:property [\n"
                "    sh:path ex:age ;\n    sh:minInclusive 0 ;\n    sh:message \"Age must be non-negative\" ;\n  ] ."
            ),
            "facts_ttl": "@prefix ex: <http://example.org/> .\n\nex:alice a ex:Person ;\n  ex:age -1 .",
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
            "reasoning": "e2e: finalizing after SHACL violation observation relay",
            "subject": "Re: e2e reply",
            "verification_summary": "e2e: formal_reason reported conforms false",
            "content": "e2e-formal-reason-violation-verified-answer",
        },
    ),
]

FORMAL_REASON_VIOLATION_SPEC = MailflowScenarioSpec(
    label="formal_reason_violation",
    raw_id_prefix="e2e-formal-reason-viol-",
    stub_dir=_WIREMOCK_STUBS_ROOT / "test_formal_reason_violation_e2e",
    stub_tag="stub-formal-reason-violation-01",
    body_head=(
        f"{E2E_FORMAL_REASON_VIOLATION_BODY}\n"
        "e2e formal_reason SHACL violation test body"
    ),
    min_chat_completion_posts=3,
    min_embedding_posts=1,
    min_rerank_posts=0,
    reply_body_needle="e2e-formal-reason-violation-verified-answer",
    reasoning_phases=_VIOLATION_PHASES,
)



def test_formal_reason_violation_full_pipeline(
    e2e_runtime: E2EComposeRuntime,
) -> None:
    """SHACL violation → observation conforms: False → response_finalize."""
    with mailflow_inject_and_wait(FORMAL_REASON_VIOLATION_SPEC, e2e_runtime.project_name) as (
        project,
        raw_id,
        _canonical_id,
        nm_inner,
        stub_tag,
        correlation_key,
    ):
        try:
            assert_full_mailflow_pipeline(
                FORMAL_REASON_VIOLATION_SPEC,
                project=project,
                raw_id=raw_id,
                nm_inner=nm_inner,
                stub_tag=stub_tag,
                correlation_key=correlation_key,
            )
            rt = discover_runtime(project, repo_root=REPO_ROOT)
            wm_base = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
            # Detag (§3.6.8): additive presence — addLast в saw-<kebab>-<thread-root> при маркере в теле.
            def _seen(name: str) -> int:
                return wiremock_state_thread_root_list_size(
                    wm_base, f"{name.replace('_', '-')}-{correlation_key}"
                )

            assert _seen("saw_conforms_false") >= 1, (
                "formal_reason observation (conforms: False) must reach reasoning (state saw_conforms_false)"
            )
            assert _seen("saw_violations") >= 1, (
                "formal_reason violations must reach reasoning (state saw_violations)"
            )
            # Нарушение SHACL само по себе НЕ активирует formal_reason-гейт.
            assert _seen("saw_gate_active") == 0, (
                "formal_reason gate must NOT activate on a plain SHACL violation (state saw_gate_active)"
            )
            log.info("formal_reason_violation_observation_verified", correlation_key=correlation_key)
        except Exception:
            log.debug(
                "failure_artifacts",
                body=clip_log_body(dump_failure_artifacts(project, repo_root=REPO_ROOT)),
            )
            raise
