"""E2E: formal_reason QUERY ERROR → technical gate → retry → finalize.

Сценарий: reasoning → formal_reason (битый SPARQL) → enrich_fast → reasoning
(gate: только formal_reason + memory_query) → formal_reason (исправленный query) →
enrich_fast → reasoning (полный toolset) → tasks_upsert → response_finalize.

Стабы: ``wiremock_stubs/test_formal_reason_technical_gate_e2e/``.
"""
from __future__ import annotations

from pathlib import Path


from .log import clip_log_body, log
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
E2E_FORMAL_REASON_TECH_GATE_BODY = "E2E-FORMAL-REASON-TECH-GATE-BODY"

# Detag: gated formal_reason — PER-TEST latch reasoning-стабы (swap→pure, БЕЗ журнала, content-flags).
# Слепой generic phase-counter НЕ моделирует гейт (под активным гейтом FSM отвергает finalize → ре-диспатч
# → пере-хоп → краш). Latch-стабы (100/101/102/103) матчат по ТЕЛУ ('Gate retry counter:'/'QUERY ERROR'/
# 'query_result:') + фазовым защёлкам на ЧИСТОМ thread-root → корректно следуют гейту, без слепого счётчика.
# Проверки — по STATE-флагам (gate_at_phase0/saw_gate_active/saw_query_error/gated_has_finalize/
# saw_query_result), которые пишут сами latch-стабы. Без journal, без stub_tag-гейтинга, без generic-фаз.
FORMAL_REASON_TECH_GATE_SPEC = MailflowScenarioSpec(
    label="formal_reason_technical_gate",
    raw_id_prefix="e2e-formal-reason-tech-gate-",
    stub_dir=_WIREMOCK_STUBS_ROOT / "test_formal_reason_technical_gate_e2e",
    stub_tag="stub-formal-reason-technical-gate-01",
    body_head=(
        f"{E2E_FORMAL_REASON_TECH_GATE_BODY}\n"
        "e2e formal_reason QUERY ERROR technical gate test body"
    ),
    min_chat_completion_posts=4,
    min_reasoning_chat_completion_posts=2,
    min_embedding_posts=1,
    min_rerank_posts=0,
    reply_body_needle="e2e-formal-reason-tech-gate-verified-answer",
)


def test_formal_reason_technical_gate_full_pipeline(
    e2e_runtime: E2EComposeRuntime,
) -> None:
    with mailflow_inject_and_wait(FORMAL_REASON_TECH_GATE_SPEC, e2e_runtime.project_name) as (
        project,
        raw_id,
        _canonical_id,
        nm_inner,
        stub_tag,
        correlation_key,
    ):
        try:
            assert_full_mailflow_pipeline(
                FORMAL_REASON_TECH_GATE_SPEC,
                project=project,
                raw_id=raw_id,
                nm_inner=nm_inner,
                stub_tag=stub_tag,
                correlation_key=correlation_key,
            )
            rt = discover_runtime(project, repo_root=REPO_ROOT)
            wm_base = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
            # Detag (§3.6.8): гейт-хопы по (call-site + req_seq); saw_*/gate_* — additive presence
            # (стаб делает addLast в `<kebab>-<thread-root>` при маркере в теле), читаем размер списка.
            def _seen(name: str) -> int:
                return wiremock_state_thread_root_list_size(
                    wm_base, f"{name.replace('_', '-')}-{correlation_key}"
                )

            # 1-й reasoning (фаза 0) — ДО активации гейта (гейт ещё не вставлен).
            assert _seen("gate_at_phase0") == 0, (
                "first FSM reasoning must be ungated (state gate_at_phase0)"
            )
            # QUERY ERROR от невалидного SPARQL дошёл до reasoning И активировал гейт.
            assert _seen("saw_query_error") >= 1, "QUERY ERROR must reach reasoning (state saw_query_error)"
            assert _seen("saw_gate_active") >= 1, (
                "QUERY ERROR must activate the formal_reason gate (state saw_gate_active)"
            )
            # Под активным гейтом finalize НЕ предлагается (owner-выбор: gated → нет finalize).
            assert _seen("gated_has_finalize") == 0, (
                "gated reasoning must NOT offer response_finalize (state gated_has_finalize)"
            )
            # Исправленный запрос вернул query_result → ungated finalize.
            assert _seen("saw_query_result") >= 1, (
                "corrected formal_reason query_result must reach reasoning (state saw_query_result)"
            )
        except Exception:
            log.error(
                "formal_reason_technical_gate_failed",
                body=clip_log_body(dump_failure_artifacts(project, repo_root=REPO_ROOT)),
            )
            raise
