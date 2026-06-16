"""E2E: IRT whitelist unified context — observe seed thread + turn2 reply.

Turn 1: append → observe → finalize (``stub-unified-context-roles-01``).
Turn 2: reply in same thread; reasoning journal must include ingress/observe markers
and must not include enrich-service leak marker.
"""
from __future__ import annotations

import uuid
from pathlib import Path


from tests.e2e.log import clip_log_body, log

from .toolkit import (
    E2EComposeRuntime,
    E2EComposeRuntime,
    MailflowScenarioSpec,
    REPO_ROOT,
    assert_full_mailflow_pipeline,
    discover_runtime,
    dump_failure_artifacts,
    greenmail_wait_agent_reply_message_id,
    e2e_dense_threlium_ctx_body,
    email_ingress_notmuch_id_inner,
    mailflow_inject_and_wait,
    mailflow_wait_fsm_maildir_activity,
    smtp_inject_inbound,
    wait_for_greenmail_inbox_message_gone_host,
    wait_for_greenmail_user_reply,
)
from .wiremock_client import (
    wiremock_public_base,
    wiremock_state_thread_root_property,
)

_WIREMOCK_STUBS_ROOT = Path(__file__).resolve().parent / "wiremock_stubs"
E2E_ROLE_INGRESS_SEED = "E2E-ROLE-INGRESS-SEED"
E2E_ROLE_INGRESS_TURN2 = "E2E-ROLE-INGRESS-TURN2"

UNIFIED_CONTEXT_TURN1_SPEC = MailflowScenarioSpec(
    label="unified_context_roles_turn1",
    raw_id_prefix="e2e-uctx-roles-seed-",
    stub_dir=_WIREMOCK_STUBS_ROOT / "test_unified_context_roles_e2e",
    stub_tag="stub-unified-context-roles-01",
    body_head=f"{E2E_ROLE_INGRESS_SEED}\ne2e unified context roles seed",
    min_chat_completion_posts=5,
    min_embedding_posts=1,
    min_rerank_posts=0,
    reply_body_needle="e2e",
)


def test_unified_context_roles_two_turn(e2e_runtime: E2EComposeRuntime) -> None:
    """Turn1 observe cycle; turn2 reasoning context = deduped <history> stream (no relay blob)."""
    project = e2e_runtime.project_name
    try:
        with mailflow_inject_and_wait(UNIFIED_CONTEXT_TURN1_SPEC, project) as (
            _p,
            seed_raw,
            _canon,
            seed_nm,
            stub_tag,
            correlation_key,
        ):
            assert_full_mailflow_pipeline(
                UNIFIED_CONTEXT_TURN1_SPEC,
                project=project,
                raw_id=seed_raw,
                nm_inner=seed_nm,
                stub_tag=stub_tag,
                correlation_key=correlation_key,
            )

            rt = discover_runtime(project, repo_root=REPO_ROOT)
            wm_base = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
            agent_reply_mid = greenmail_wait_agent_reply_message_id(
                rt.greenmail_imap_host,
                rt.greenmail_imap_port,
                in_reply_to_anchor=seed_raw,
                body_substring=UNIFIED_CONTEXT_TURN1_SPEC.reply_body_needle or "e2e",
            )
            raw2 = f"e2e-uctx-roles-turn2-{uuid.uuid4().hex}@localhost"
            smtp_inject_inbound(
                project,
                checkout="/unused",
                repo_root=REPO_ROOT,
                message_id=raw2,
                in_reply_to=agent_reply_mid,
                body=e2e_dense_threlium_ctx_body(
                    head=f"{E2E_ROLE_INGRESS_TURN2}\ne2e unified context roles turn2",
                    correlation_key=correlation_key,
                ),
            )
            wait_for_greenmail_inbox_message_gone_host(
                rt.greenmail_imap_host, rt.greenmail_imap_port, message_id=raw2
            )
            nm2 = email_ingress_notmuch_id_inner(raw2)
            mailflow_wait_fsm_maildir_activity(
                project, repo_root=REPO_ROOT, message_id=nm2
            )
            wait_for_greenmail_user_reply(
                project,
                raw_id=raw2,
                repo_root=REPO_ROOT,
                body_substring="e2e-unified-context-roles-verified",
            )

            # Detag (§3.6.2): без journal-скана. Reasoning-стабы (pure thread-root) на лету пишут
            # липкие STATE-флаги: whole-body наличие маркеров + СЕКЦИОННЫЕ (regexExtract секции
            # <conversation_history>) — точность «внутри секции» сохранена (см. E2E.md §3.6.2).
            def _flag(name: str) -> str:
                return wiremock_state_thread_root_property(wm_base, correlation_key, name)

            assert _flag("saw_seed") == "1", "turn1 ingress marker missing from reasoning (state saw_seed)"
            assert _flag("saw_turn2") == "1", "turn2 ingress marker missing from reasoning (state saw_turn2)"
            assert _flag("saw_observed") == "1", "observe chunk missing from reasoning context (state saw_observed)"
            # conversation_history несёт ingress-distill заголовки (## User intent) — секционно.
            assert _flag("hist_user_intent") == "1", (
                "conversation_history must carry ingress distill headings from <history> parts (state hist_user_intent)"
            )
            # enrich_fast relay blob НЕ должен попасть в conversation_history (content-CID dedup; To: не рендерится).
            assert _flag("hist_leak") == "0", (
                "enrich_fast relay blob must not appear in <conversation_history> (state hist_leak)"
            )
            # Durability: turn-1 observe-chunk обязан выжить в <conversation_history> (full enrich turn2
            # собирает его из <history>-частей треда), а не только в fast-cycle <conversation_delta>.
            assert _flag("hist_observed") == "1", (
                "turn-1 observe output missing from <conversation_history>: full enrich did not collect "
                "it from thread <history>-parts (state hist_observed)"
            )
            log.info("unified_context_roles_ok", correlation_key=correlation_key)
    except Exception:
        log.debug(
            "failure_artifacts",
            body=clip_log_body(dump_failure_artifacts(project, repo_root=REPO_ROOT)),
        )
        raise
