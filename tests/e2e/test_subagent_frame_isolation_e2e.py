"""E2E: subagent frame isolation (IrtSubagentMarker) on compose stack.

Two scenarios (separate stub_tag / WireMock state per test):

1. **Response buffer** — L0 ``response_append`` chunk must not appear in L1
   ``response_finalize`` LLM prompt (per-frame ``stop_at_route`` collect).
2. **Task-ledger** — L0 open subtask must not block L1 finalize gate (per-frame
   task collect without parent ledger).

Both invariants are verified from **WireMock state** (append-only, recorded on the fly
by the serving stub — §3.6.2/§3.6.3), NOT a journal scan: the journal ring (~1000
entries) evicts our entries under ``-n12`` and a one-shot scan races the L1 finalize
record. The L1 finalize stub (``113``) itself computes the content-flags; the test reads
them after the GreenMail reply barrier (happens-after L1 finalize → direct read,
time-independent). This is also the *specific* L1-finalize barrier (``l1_finalize_seen``)
the old ``>=20 chat call-sites`` proxy could not give.
"""
from __future__ import annotations

from pathlib import Path


from tests.e2e.log import clip_log_body, log

from .toolkit import (
    E2EComposeRuntime,
    MailflowScenarioSpec,
    REPO_ROOT,
    assert_full_mailflow_pipeline,
    discover_runtime,
    dump_failure_artifacts,
    mailflow_inject_and_wait,
)
from .wiremock_client import (
    wiremock_public_base,
    wiremock_state_thread_root_property,
)

_WIREMOCK_STUBS_ROOT = Path(__file__).resolve().parent / "wiremock_stubs"
E2E_SUBAGENT_ISO_BODY = "E2E-SUBAGENT-FRAME-ISO"
E2E_L0_BUFFER_MARKER = "E2E-ISO-L0-BUFFER-CHUNK-MUST-NOT-LEAK-TO-L1"

RESPONSE_ISO_SPEC = MailflowScenarioSpec(
    label="subagent_response_frame_iso",
    raw_id_prefix="e2e-subagent-resp-iso-",
    stub_dir=_WIREMOCK_STUBS_ROOT / "test_subagent_frame_isolation_e2e",
    stub_tag="stub-subagent-frame-iso-01",
    body_head=f"{E2E_SUBAGENT_ISO_BODY}\ne2e subagent response buffer frame isolation",
    min_chat_completion_posts=12,  # generic lifecycle floor; the SPECIFIC L1-finalize barrier is l1_finalize_seen state
    min_embedding_posts=1,
    min_rerank_posts=0,
    reply_body_needle="e2e-subagent-frame-iso-verified",
    wiremock_journal_ready_needle="call_e2e_iso_l0_finalize",
)

LEDGER_ISO_SPEC = MailflowScenarioSpec(
    label="subagent_ledger_frame_iso",
    raw_id_prefix="e2e-subagent-ledger-iso-",
    stub_dir=_WIREMOCK_STUBS_ROOT / "test_subagent_ledger_isolation_e2e",
    stub_tag="stub-subagent-ledger-iso-01",
    body_head=f"{E2E_SUBAGENT_ISO_BODY}\ne2e subagent task-ledger frame isolation",
    min_chat_completion_posts=12,  # generic lifecycle floor; the SPECIFIC L1-finalize barrier is l1_finalize_seen state
    min_embedding_posts=1,
    min_rerank_posts=0,
    reply_body_needle="e2e-subagent-frame-iso-verified",
    wiremock_journal_ready_needle="call_e2e_iso_l0_finalize",
)


def _assert_l1_finalize_fired(wm: str, *, stub_tag: str, correlation_key: str) -> None:
    """Specific L1-finalize barrier: ``113_l1_finalize`` recorded ``l1_finalize_seen`` into its phase
    context. Replaces the ``>=20 chat call-sites`` proxy (which passes before — or independently of —
    the L1 finalize record). State is append-only → immune to journal eviction under ``-n12``."""
    phase_ctx = f"{stub_tag}::{correlation_key}"
    seen = wiremock_state_thread_root_property(wm, phase_ctx, "l1_finalize_seen")
    assert seen == "1", (
        f"L1 finalize hop did not record (l1_finalize_seen={seen!r}, ctx={phase_ctx!r}) — "
        "frame chain never reached response_finalize"
    )


def test_subagent_response_buffer_frame_isolation(e2e_runtime: E2EComposeRuntime) -> None:
    """L0 append chunk must not appear in L1 finalize LLM prompt."""
    project = e2e_runtime.project_name
    try:
        with mailflow_inject_and_wait(RESPONSE_ISO_SPEC, project) as (
            _p,
            raw_id,
            _canon,
            nm_inner,
            stub_tag,
            correlation_key,
        ):
            assert_full_mailflow_pipeline(
                RESPONSE_ISO_SPEC,
                project=project,
                raw_id=raw_id,
                nm_inner=nm_inner,
                stub_tag=stub_tag,
                correlation_key=correlation_key,
            )
            rt = discover_runtime(project, repo_root=REPO_ROOT)
            wm = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
            # Direct read after the GreenMail reply barrier (happens-after L1 finalize). §3.6.2.
            _assert_l1_finalize_fired(wm, stub_tag=stub_tag, correlation_key=correlation_key)
            phase_ctx = f"{stub_tag}::{correlation_key}"
            leaked = wiremock_state_thread_root_property(wm, phase_ctx, "l1_l0_leaked")
            assert leaked == "0", (
                "L0 response_append buffer leaked into L1 frame reasoning prompt "
                f"(IrtSubagentMarker / stop_at_route isolation regression; l1_l0_leaked={leaked!r})"
            )
            log.info("subagent_response_frame_iso_l1_prompt_verified", stub_tag=stub_tag)
    except Exception:
        log.debug(
            "failure_artifacts",
            body=clip_log_body(dump_failure_artifacts(project, repo_root=REPO_ROOT)),
        )
        raise


def test_subagent_task_ledger_frame_isolation(e2e_runtime: E2EComposeRuntime) -> None:
    """L0 open subtask must not block L1 finalize (isolated per-frame ledger)."""
    project = e2e_runtime.project_name
    try:
        with mailflow_inject_and_wait(LEDGER_ISO_SPEC, project) as (
            _p,
            raw_id,
            _canon,
            nm_inner,
            stub_tag,
            correlation_key,
        ):
            assert_full_mailflow_pipeline(
                LEDGER_ISO_SPEC,
                project=project,
                raw_id=raw_id,
                nm_inner=nm_inner,
                stub_tag=stub_tag,
                correlation_key=correlation_key,
            )
            rt = discover_runtime(project, repo_root=REPO_ROOT)
            wm = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
            # Direct read after the GreenMail reply barrier (happens-after L1 finalize). §3.6.2.
            _assert_l1_finalize_fired(wm, stub_tag=stub_tag, correlation_key=correlation_key)
            # Isolation invariant: the L0 parent-frame open subtask must not be collected into the L1
            # frame ledger — so its text must NOT appear in the L1 finalize reasoning prompt (113 body).
            # If it leaked, the L1 finalize gate would see L0's open subtask and block. The L1 finalize
            # stub computes the content-flag from its own body (single-writer, append-only state) — same
            # idiom as the response-buffer leak check above. (113 fires once: hasNotProperty phase_l1_done.)
            phase_ctx = f"{stub_tag}::{correlation_key}"
            leaked = wiremock_state_thread_root_property(wm, phase_ctx, "l1_l0_ledger_leaked")
            assert leaked == "0", (
                "L0 parent-frame open subtask leaked into L1 frame ledger / finalize prompt — frame "
                f"isolation regression (per-frame task collect; l1_l0_ledger_leaked={leaked!r})"
            )
    except Exception:
        log.debug(
            "failure_artifacts",
            body=clip_log_body(dump_failure_artifacts(project, repo_root=REPO_ROOT)),
        )
        raise
