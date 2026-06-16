"""E2E: formal_reason with RDFS inference and return_derived → finalize.

Сценарий: reasoning → formal_reason (inference=rdfs, return_derived=true) →
enrich_fast → reasoning → response_finalize.

Покрытие:
- formal_reason возвращает derived_triples в observation-note
- min 2 chat completion (formal_reason + finalize)
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
from .wiremock_client import wiremock_public_base, wiremock_state_thread_root_property

_WIREMOCK_STUBS_ROOT = Path(__file__).resolve().parent / "wiremock_stubs"
E2E_FORMAL_REASON_INFERENCE_BODY = "E2E-FORMAL-REASON-INFERENCE-BODY"

# Detag (§3.6.8): generic reasoning. RDFS-inference flow вызывает formal_reason дважды (logic + повторный
# re-complete в том же _decide до прихода derived_triples), затем tasks_upsert → finalize (min_chat=4).
# Гейт не активируется. Запас finalize-фаз + absorbing-207 покрывают возможный лишний re-complete.
_FR_INFER = {
    "reasoning": "e2e: RDFS subclass inference — read derived triples",
    "inference": "rdfs",
    "return_derived": True,
    "ontology_ttl": (
        "@prefix ex: <http://example.org/> .\n@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n\n"
        "ex:Employee rdfs:subClassOf ex:Person .\n"
    ),
    "shapes_ttl": (
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n@prefix ex: <http://example.org/> .\n\n"
        "ex:PersonShape a sh:NodeShape ;\n  sh:targetClass ex:Person ;\n"
        "  sh:property [ sh:path ex:name ; sh:minCount 1 ] ."
    ),
    "facts_ttl": '@prefix ex: <http://example.org/> .\n\nex:alice a ex:Employee ;\n  ex:name "Alice" .\n',
}
_INFERENCE_PHASES = [
    ("formal_reason", dict(_FR_INFER)),
    ("formal_reason", dict(_FR_INFER)),
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
            "reasoning": "e2e: finalizing after formal_reason with derived triples",
            "subject": "Re: e2e reply",
            "verification_summary": "e2e: formal_reason returned derived_triples from RDFS inference",
            "content": "e2e-formal-reason-inference-verified-answer",
        },
    ),
]

FORMAL_REASON_INFERENCE_SPEC = MailflowScenarioSpec(
    label="formal_reason_inference",
    raw_id_prefix="e2e-formal-reason-inference-",
    stub_dir=_WIREMOCK_STUBS_ROOT / "test_formal_reason_inference_e2e",
    stub_tag="stub-formal-reason-inference-01",
    body_head=(
        f"{E2E_FORMAL_REASON_INFERENCE_BODY}\n"
        "e2e formal_reason inference derived triples test body"
    ),
    min_chat_completion_posts=4,
    min_reasoning_chat_completion_posts=2,
    min_embedding_posts=1,
    min_rerank_posts=0,
    reply_body_needle="e2e-formal-reason-inference-verified-answer",
    reasoning_phases=_INFERENCE_PHASES,
)


def test_formal_reason_inference_full_pipeline(
    e2e_runtime: E2EComposeRuntime,
) -> None:
    with mailflow_inject_and_wait(FORMAL_REASON_INFERENCE_SPEC, e2e_runtime.project_name) as (
        project,
        raw_id,
        _canonical_id,
        nm_inner,
        stub_tag,
        correlation_key,
    ):
        try:
            assert_full_mailflow_pipeline(
                FORMAL_REASON_INFERENCE_SPEC,
                project=project,
                raw_id=raw_id,
                nm_inner=nm_inner,
                stub_tag=stub_tag,
                correlation_key=correlation_key,
            )
            rt = discover_runtime(project, repo_root=REPO_ROOT)
            wm_base = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
            # Detag (§3.6.2): без journal/stub_tag — гейт formal_reason не активировался (STATE).
            assert (
                wiremock_state_thread_root_property(wm_base, correlation_key, "saw_gate_active") == "0"
            ), "formal_reason gate must NOT activate for valid RDFS inference (state saw_gate_active)"
        except Exception:
            log.debug(
                "failure_artifacts",
                body=clip_log_body(dump_failure_artifacts(project, repo_root=REPO_ROOT)),
            )
            raise
