"""E2E тест инкрементальной сборки ответа (RESPONSE_TABLE.md).

Сценарий: reasoning → response_append (×2) → response_finalize (Mode 2: buffer only).
Каждый response_append проходит enrich_fast → reasoning (быстрый цикл).
Финальный ответ формируется из буфера (два чанка) без inline content.

Стабы используют фазовый автомат WireMock State Extension:
- phase_append_1: после первого reasoning → response_append
- phase_append_2: после второго reasoning → response_append
- Третий reasoning видит phase_append_2 → response_finalize
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.log import clip_log_body, log
from threlium.types import FsmStage

from .helpers import (
    MailflowScenarioSpec,
    assert_full_mailflow_pipeline,
    dump_failure_artifacts,
    mailflow_inject_and_wait,
    REPO_ROOT,
)

_WIREMOCK_STUBS_ROOT = Path(__file__).resolve().parent / "wiremock_stubs"
E2E_RESPONSE_BUFFER_BODY_MARKER = "E2E-RESPONSE-BUFFER-BODY-MARKER"

RESPONSE_BUFFER_SPEC = MailflowScenarioSpec(
    label="response_buffer",
    raw_id_prefix="e2e-respbuf-",
    stub_dir=_WIREMOCK_STUBS_ROOT / "test_response_buffer_e2e",
    stub_tag="stub-response-buffer-e2e-01",
    body_head=f"{E2E_RESPONSE_BUFFER_BODY_MARKER}\ne2e response buffer accumulation test body",
    min_chat_completion_posts=3,
    min_embedding_posts=1,
    expect_notmuch_stage_folders=(
        FsmStage.INGRESS.value,
        FsmStage.ENRICH.value,
        FsmStage.REASONING.value,
        FsmStage.RESPONSE_APPEND.value,
        FsmStage.ENRICH_FAST.value,
        FsmStage.RESPONSE_FINALIZE.value,
        FsmStage.EGRESS_ROUTER.value,
        FsmStage.EGRESS_EMAIL.value,
        FsmStage.ARCHIVE.value,
    ),
    reply_body_needle="e2e-chunk-first",
)


@pytest.fixture()
def response_buffer_processed_stack(deployed_stack: str) -> object:
    """WireMock (response_buffer) → inject → \\Seen → FSM activity."""
    with mailflow_inject_and_wait(RESPONSE_BUFFER_SPEC, deployed_stack) as ids:
        yield ids


@pytest.mark.e2e
@pytest.mark.e2e_live
@pytest.mark.mailflow
def test_response_buffer_accumulation_full_pipeline(
    response_buffer_processed_stack: tuple[str, str, str, str, str, str],
) -> None:
    """RESPONSE_TABLE: reasoning → 2× response_append (enrich_fast loop) → response_finalize (Mode 2)."""
    project, raw_id, _canonical_id, nm_inner, stub_tag, correlation_key = (
        response_buffer_processed_stack
    )
    try:
        assert_full_mailflow_pipeline(
            RESPONSE_BUFFER_SPEC,
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
