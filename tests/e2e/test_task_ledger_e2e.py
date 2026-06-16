"""E2E task-ledger variants (fail-closed gate) — parametrized mailflow scenarios on live stack.

Consolidates chain / empty-blocked / all-cancelled / upsert-error / bypass scenarios.
Each variant keeps its own WireMock stub directory; shared LightRAG stubs remain duplicated
per directory (identical JSON, scenario-specific reasoning phases only).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.log import clip_log_body, log

from .toolkit import (
    E2EComposeRuntime,
    MailflowScenarioSpec,
    assert_full_mailflow_pipeline,
    dump_failure_artifacts,
    mailflow_inject_and_wait,
    REPO_ROOT,
)

_WIREMOCK_STUBS_ROOT = Path(__file__).resolve().parent / "wiremock_stubs"

TASK_LEDGER_SPECS: tuple[MailflowScenarioSpec, ...] = (
    MailflowScenarioSpec(
        label="task_ledger_chain",
        raw_id_prefix="e2e-task-ledger-chain-",
        stub_dir=_WIREMOCK_STUBS_ROOT / "test_task_ledger_chain_e2e",
        stub_tag="stub-task-ledger-chain-01",
        body_head="E2E-TASK-LEDGER-CHAIN-BODY\ne2e task ledger chain anti-drift test body",
        min_chat_completion_posts=4,
        min_embedding_posts=1,
        min_rerank_posts=0,
        reply_body_needle="e2e-task-ledger-verified",
        wiremock_journal_ready_needle="call_e2e_finalize_ok",
    ),
    MailflowScenarioSpec(
        label="task_ledger_bypass",
        raw_id_prefix="e2e-task-ledger-bypass-",
        stub_dir=_WIREMOCK_STUBS_ROOT / "test_task_ledger_bypass_e2e",
        stub_tag="stub-task-ledger-bypass-01",
        body_head="E2E-TASK-LEDGER-BYPASS-BODY\ne2e task ledger blocker bypass test body",
        min_chat_completion_posts=2,
        min_embedding_posts=1,
        min_rerank_posts=0,
        reply_body_needle="e2e-task-ledger-bypass-verified",
        wiremock_journal_ready_needle="call_e2e_finalize_bypass_ok",
    ),
    MailflowScenarioSpec(
        label="task_ledger_empty_blocked",
        raw_id_prefix="e2e-task-ledger-empty-",
        stub_dir=_WIREMOCK_STUBS_ROOT / "test_task_ledger_empty_blocked_e2e",
        stub_tag="stub-task-ledger-empty-01",
        body_head="E2E-TASK-LEDGER-EMPTY-BODY\ne2e task ledger empty-blocked fail-closed test body",
        min_chat_completion_posts=3,
        min_embedding_posts=1,
        min_rerank_posts=0,
        reply_body_needle="e2e-task-ledger-empty-verified",
        wiremock_journal_ready_needle="call_e2e_empty_finalize_ok",
    ),
    MailflowScenarioSpec(
        label="task_ledger_all_cancelled",
        raw_id_prefix="e2e-task-ledger-allcancel-",
        stub_dir=_WIREMOCK_STUBS_ROOT / "test_task_ledger_all_cancelled_e2e",
        stub_tag="stub-task-ledger-allcancel-01",
        body_head="E2E-TASK-LEDGER-ALLCANCEL-BODY\ne2e task ledger all-cancelled guard test body",
        min_chat_completion_posts=4,
        min_embedding_posts=1,
        min_rerank_posts=0,
        reply_body_needle="e2e-task-ledger-all-cancelled-verified",
        wiremock_journal_ready_needle="call_e2e_allcancel_finalize_ok",
    ),
    MailflowScenarioSpec(
        label="task_ledger_upsert_error",
        raw_id_prefix="e2e-task-ledger-upserterr-",
        stub_dir=_WIREMOCK_STUBS_ROOT / "test_task_ledger_upsert_error_e2e",
        stub_tag="stub-task-ledger-upserterr-01",
        body_head="E2E-TASK-LEDGER-UPSERTERR-BODY\ne2e task ledger upsert-error validation test body",
        min_chat_completion_posts=3,
        min_embedding_posts=1,
        min_rerank_posts=0,
        reply_body_needle="e2e-task-ledger-upsert-error-verified",
        wiremock_journal_ready_needle="call_e2e_upserterr_finalize_ok",
    ),
    MailflowScenarioSpec(
        # No-op tasks_upsert (empty new_subtasks AND subtask_updates): pre-fix this hard-errored and
        # re-dispatched forever (production livelock). The handler must render a guidance notice and
        # relay it back via enrich_fast so reasoning corrects itself and finalizes. 101_ok gates on
        # the rendered guidance text appearing in the reasoning prompt → proves the soft path, not
        # just "recovered somehow".
        label="task_ledger_noop",
        raw_id_prefix="e2e-task-ledger-noop-",
        stub_dir=_WIREMOCK_STUBS_ROOT / "test_task_ledger_noop_e2e",
        stub_tag="stub-task-ledger-noop-01",
        body_head="E2E-TASK-LEDGER-NOOP-BODY\ne2e task ledger no-op upsert guidance test body",
        min_chat_completion_posts=3,
        min_embedding_posts=1,
        min_rerank_posts=0,
        reply_body_needle="e2e-task-ledger-noop-verified",
        wiremock_journal_ready_needle="call_e2e_noop_finalize_ok",
    ),
)


_TASK_LEDGER_PHASES = {
    'task_ledger_bypass': [
        ('tasks_upsert', {'reasoning': 'e2e: A done, B blocked externally — allow finalize with blocker', 'subtask_updates': [{'content_id': 'e4a7f48a17f1', 'status': 'done'}], 'blockers': 'e2e external dependency unavailable for subtask B', 'allow_finalize_with_blocker': True}),
        ('response_finalize', {'reasoning': 'e2e: finalize with open B allowed via allow_finalize_with_blocker', 'subject': 'Re: e2e reply', 'verification_summary': 'e2e: bypass gate — A done, B blocked but finalize permitted', 'content': 'e2e-task-ledger-bypass-verified'}),
    ],
    'task_ledger_empty_blocked': [
        ('response_finalize', {'reasoning': 'e2e: finalize attempt on an empty ledger', 'subject': 'Re: e2e reply', 'verification_summary': 'e2e: should be blocked by the fail-closed empty-ledger gate', 'content': 'e2e-empty-blocked-attempt'}),
        ('tasks_upsert', {'reasoning': 'e2e: trivial answer, record one subtask as done', 'new_subtasks': [{'text': 'Answer the user question about the task ledger', 'status': 'done'}], 'subtask_updates': [{'content_id': '481a8275734a', 'status': 'done'}]}),
        ('response_finalize', {'reasoning': 'e2e: one subtask done, gate passes', 'subject': 'Re: e2e reply', 'verification_summary': 'e2e: ledger now records one done subtask', 'content': 'e2e-task-ledger-empty-verified'}),
    ],
    'task_ledger_all_cancelled': [
        ('tasks_upsert', {'reasoning': 'e2e: cancel both subtasks (scope dropped)', 'subtask_updates': [{'content_id': '8a1ea957bf2b', 'status': 'cancelled'}, {'content_id': 'a132885eea74', 'status': 'cancelled'}]}),
        ('response_finalize', {'reasoning': 'e2e: finalize attempt with everything cancelled and nothing done', 'subject': 'Re: e2e reply', 'verification_summary': 'e2e: should be blocked by the all-cancelled guard', 'content': 'e2e-allcancel-blocked-attempt'}),
        ('tasks_upsert', {'reasoning': 'e2e: record the real completed answer as a done subtask', 'new_subtasks': [{'text': 'Answer the user question about the task ledger', 'status': 'done'}]}),
        ('response_finalize', {'reasoning': 'e2e: one subtask done alongside cancelled ones, gate passes', 'subject': 'Re: e2e reply', 'verification_summary': 'e2e: ledger has a done subtask (cancelled no longer block)', 'content': 'e2e-task-ledger-all-cancelled-verified'}),
    ],
    'task_ledger_upsert_error': [
        ('tasks_upsert', {'reasoning': 'e2e: update a non-existent content_id (should be rejected)', 'subtask_updates': [{'content_id': 'deadbeefdead', 'status': 'done'}]}),
        ('tasks_upsert', {'reasoning': 'e2e: update the real seeded subtask to done', 'subtask_updates': [{'content_id': '9d9d077ccc56', 'status': 'done'}]}),
        ('response_finalize', {'reasoning': 'e2e: seeded subtask done, gate passes', 'subject': 'Re: e2e reply', 'verification_summary': 'e2e: ledger closed after correcting the content_id', 'content': 'e2e-task-ledger-upsert-error-verified'}),
    ],
    'task_ledger_noop': [
        ('tasks_upsert', {'reasoning': 'e2e: no-op tasks_upsert — only reasoning, no new_subtasks and no subtask_updates (must be guided back, not hard-errored)'}),
        ('tasks_upsert', {'reasoning': 'e2e: update the real seeded subtask to done', 'subtask_updates': [{'content_id': '9d9d077ccc56', 'status': 'done'}]}),
        ('response_finalize', {'reasoning': 'e2e: seeded subtask done, gate passes', 'subject': 'Re: e2e reply', 'verification_summary': 'e2e: ledger closed after correcting the content_id', 'content': 'e2e-task-ledger-noop-verified'}),
    ],
}
import dataclasses as _dc  # noqa: E402
TASK_LEDGER_SPECS = tuple(
    _dc.replace(s, reasoning_phases=_TASK_LEDGER_PHASES[s.label])
    if s.label in _TASK_LEDGER_PHASES else s
    for s in TASK_LEDGER_SPECS
)


@pytest.mark.parametrize("spec", TASK_LEDGER_SPECS, ids=[s.label for s in TASK_LEDGER_SPECS])
def test_task_ledger_variant_full_pipeline(
    e2e_runtime: E2EComposeRuntime,
    spec: MailflowScenarioSpec,
) -> None:
    """Parametrized task-ledger gate scenarios (chain / bypass / empty / all-cancel / upsert-error)."""
    try:
        with mailflow_inject_and_wait(spec, e2e_runtime.project_name) as (
            project,
            raw_id,
            _canonical_id,
            nm_inner,
            stub_tag,
            correlation_key,
        ):
            assert_full_mailflow_pipeline(
                spec,
                project=project,
                raw_id=raw_id,
                nm_inner=nm_inner,
                stub_tag=stub_tag,
                correlation_key=correlation_key,
            )
    except Exception:
        log.debug(
            "failure_artifacts",
            body=clip_log_body(
                dump_failure_artifacts(e2e_runtime.project_name, repo_root=REPO_ROOT)
            ),
        )
        raise
