"""E2E цепочки task-ledger (anti-drift) на **живом** стеке.

.. deprecated::
   Сценарии перенесены в :mod:`tests.e2e.test_task_ledger_e2e` (parametrize).
   Этот модуль сохраняет chain-only asserts (enrich subtasks in LightRAG query).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.log import clip_log_body, log

from .helpers import (
    assert_full_mailflow_pipeline,
    discover_runtime,
    dump_failure_artifacts,
    mailflow_inject_and_wait,
    REPO_ROOT,
)
from .test_task_ledger_e2e import TASK_LEDGER_SPECS
from .wiremock_client import (
    find_wiremock_requests_by_body_contains,
    wiremock_public_base,
)

_WIREMOCK_STUBS_ROOT = Path(__file__).resolve().parent / "wiremock_stubs"
E2E_TASK_LEDGER_BODY_MARKER = "E2E-TASK-LEDGER-CHAIN-BODY"
E2E_TASK_LEDGER_SEED_SUBTASK = "Locate the task ledger handler in states"

TASK_LEDGER_CHAIN_SPEC = next(s for s in TASK_LEDGER_SPECS if s.label == "task_ledger_chain")
TASK_LEDGER_BYPASS_SPEC = next(s for s in TASK_LEDGER_SPECS if s.label == "task_ledger_bypass")


def _assert_enrich_aquery_includes_seed_subtasks(project: str, stub_tag: str) -> None:
    """Enrich LightRAG aquery must include seeded subtask texts from enrich_task_plan."""
    rt = discover_runtime(project, repo_root=REPO_ROOT)
    wm_base = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
    matches = find_wiremock_requests_by_body_contains(
        wm_base, E2E_TASK_LEDGER_SEED_SUBTASK, stub_tag=stub_tag
    )
    embed_or_chat = [
        e
        for e in matches
        if any(
            tok in (e.get("request", {}).get("url") or "")
            for tok in ("/embeddings", "/chat/completions")
        )
    ]
    assert embed_or_chat, (
        f"expected enrich aquery round-trip to include seed subtask "
        f"{E2E_TASK_LEDGER_SEED_SUBTASK!r} in WireMock journal"
    )
    log.info("task_ledger_enrich_subtasks_in_query_verified", hits=len(embed_or_chat))


@pytest.fixture()
def task_ledger_chain_processed_stack(live_e2e_stack_ready: str) -> object:
    """WireMock (task_ledger_chain) -> inject -> \\Seen -> FSM activity (live stack)."""
    with mailflow_inject_and_wait(TASK_LEDGER_CHAIN_SPEC, live_e2e_stack_ready) as ids:
        yield ids


@pytest.fixture()
def task_ledger_bypass_processed_stack(live_e2e_stack_ready: str) -> object:
    """WireMock (task_ledger_bypass) -> inject -> \\Seen -> FSM activity (live stack)."""
    with mailflow_inject_and_wait(TASK_LEDGER_BYPASS_SPEC, live_e2e_stack_ready) as ids:
        yield ids


@pytest.mark.e2e
@pytest.mark.e2e_live
@pytest.mark.mailflow
def test_task_ledger_chain_full_pipeline(
    task_ledger_chain_processed_stack: tuple[str, str, str, str, str, str],
) -> None:
    """Anti-drift: seed -> tasks_upsert(add+in_progress) -> finalize BLOCKED -> close -> egress."""
    project, raw_id, _canonical_id, nm_inner, stub_tag, correlation_key = (
        task_ledger_chain_processed_stack
    )
    try:
        assert_full_mailflow_pipeline(
            TASK_LEDGER_CHAIN_SPEC,
            project=project,
            raw_id=raw_id,
            nm_inner=nm_inner,
            stub_tag=stub_tag,
            correlation_key=correlation_key,
        )
        _assert_enrich_aquery_includes_seed_subtasks(project, stub_tag)
    except Exception:
        log.debug(
            "failure_artifacts",
            body=clip_log_body(dump_failure_artifacts(project, repo_root=REPO_ROOT)),
        )
        raise


@pytest.mark.e2e
@pytest.mark.e2e_live
@pytest.mark.mailflow
def test_task_ledger_bypass_blocker_full_pipeline(
    task_ledger_bypass_processed_stack: tuple[str, str, str, str, str, str],
) -> None:
    """Gate bypass: open subtask B + blockers + allow_finalize_with_blocker → egress."""
    project, raw_id, _canonical_id, nm_inner, stub_tag, correlation_key = (
        task_ledger_bypass_processed_stack
    )
    try:
        assert_full_mailflow_pipeline(
            TASK_LEDGER_BYPASS_SPEC,
            project=project,
            raw_id=raw_id,
            nm_inner=nm_inner,
            stub_tag=stub_tag,
            correlation_key=correlation_key,
        )
    except Exception:
        log.debug(
            "failure_artifacts",
            body=clip_log_body(dump_failure_artifacts(project, repo_root=REPO_ROOT)),
        )
        raise
