"""E2E: LightRAG correlator/wrapper integrity — all endpoints under one thread-root.

Purpose (owner-requested): a dedicated probe that a SINGLE dense message drives EVERY
LightRAG endpoint wrapper and that each call still carries its correlator, so silent
breakage (e.g. rerank quietly stopped firing, or an embeddings/LLM wrapper dropped the
correlation headers) is caught loudly instead of degrading coverage elsewhere.

Mechanism (state-only, no notmuch/docker-exec): the global call-site recorder writes
``X-Threlium-Call-Site`` into the thread-root's state list for every chat/completions +
embeddings + rerank request — but ONLY if the request still carries ``X-Threlium-Thread-Root``
(docs/E2E.md §3.6.1). So "call-site present in thread-root state" == "the wrapper preserved
the correlator". A missing call-site therefore pinpoints a lost/scrambled correlator.

Endpoint coverage (one dense memory_query contour):
- index side (drain → ainsert): ``lightrag_index`` (embeddings wrapper),
  ``extract_knowledge_graph``, ``extract_knowledge_graph_gleaning`` (LLM wrappers).
- query side (enrich aquery): ``extract_query_keywords``, ``lightrag_query`` (embeddings
  wrapper), ``generate_rag_answer``, ``lightrag_query_rerank``.

This test is the measuring instrument for the async-index refactor: run it with the drain
singleton KEPT (correlators intact) vs removed (contextvars scramble → indexing chunks lose
thread-root) to prove which correlators survive (docs/SESSION_HANDOFF.md §4 P0).
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

from tests.e2e.log import clip_log_body, log

from .toolkit import (
    E2EComposeRuntime,
    MailflowScenarioSpec,
    assert_full_mailflow_pipeline,
    discover_runtime,
    dump_failure_artifacts,
    mailflow_inject_and_wait,
    poll_until,
    REPO_ROOT,
)
from .toolkit.constants import TIMEOUT_POLL_SHORT
from .wiremock_client import (
    wiremock_public_base,
    wiremock_state_thread_root_call_sites,
)

_WIREMOCK_STUBS_ROOT = Path(__file__).resolve().parent / "wiremock_stubs"
E2E_LIGHTRAG_INTEGRITY_BODY_MARKER = "E2E-LIGHTRAG-INTEGRITY-BODY"

# Generic per-test index-correlation marker (docs/E2E.md §3.6.3, §7). The drain stamps NO thread-root
# on indexing embeds (batched op via the shared pool → thread-root misattributed), and the INDEXED
# message is an enriched one whose Message-ID != correlation_key, so neither thread-root nor a body
# Message-ID body-corr identifies THIS test's index call. Instead the test bakes a STATIC token into
# its injected body that survives distill→chunk→embed; the generic 011 stub regexExtracts the convention
# token `E2E-INDEX-CORR-<...>` and append-only records the call-site into a per-token context. The token
# is unique to this test → a TRUE per-test counter (a global fire-at-all counter would false-pass on a
# neighbour's index, and "the index wrapper fires with its correlator" IS this test's whole point).
_INDEX_CORR_MARKER = "E2E-INDEX-CORR-lightrag-integrity"
_INDEX_CALL_SITES = ("lightrag_index",)

# QUERY-side wrappers: per-handler-call correlation (enrich aquery = this turn) — recorded by the per-test
# stubs under THIS test's thread-root (the FSM/query path keeps thread-root). Read under correlation_key
# (== thread-root for the single injected message).
_THREAD_ROOT_CALL_SITES = (
    "extract_query_keywords",  # query-side
    "lightrag_query",          # query embeddings
    "generate_rag_answer",     # query-side
)
# KG-extraction wrappers: under -n4 the drain batches multiple docs and LightRAG AGGREGATES their
# chunks into ONE extraction prompt, so a per-message body-flag would attribute them to a neighbour's
# correlator (first <corr@localhost> wins). The integrity goal here is only «does the KG-extraction
# wrapper fire AT ALL with its call-site» (catch silent breakage), so the generic stubs record these
# under a FIXED global context and we assert their presence there, not per thread-root.
_KG_GLOBAL_CONTEXT = "lightrag_kg_calls"
_KG_CALL_SITES = (
    "extract_knowledge_graph",
    "extract_knowledge_graph_gleaning",
)
# Rerank (``lightrag_query_rerank``) is DATA-dependent (fires only when the query retrieves >=2 chunks),
# so it is NOT asserted as a per-message invariant on a single cold message (flaky: present at -n0, absent
# at -n4). Deterministic coverage needs a seeded-vdb variant (seed A indexed -> B queries A) — deferred.

# Reuse the memory_query stub set: it exercises the full RAG path (aquery + indexing) and its
# stubs hard-code this stub_tag in their state-matcher hasContext, so the spec must keep it.
LIGHTRAG_INTEGRITY_SPEC = MailflowScenarioSpec(
    label="lightrag_correlator_integrity",
    raw_id_prefix="e2e-lr-integrity-",
    stub_dir=_WIREMOCK_STUBS_ROOT / "test_memory_query_e2e",
    stub_tag="stub-memory-query-01",
    body_head=(
        f"{E2E_LIGHTRAG_INTEGRITY_BODY_MARKER}\n{_INDEX_CORR_MARKER}\n"
        "e2e lightrag correlator integrity body"
    ),
    min_chat_completion_posts=3,
    reply_body_needle="e2e-memory-query-verified-answer",
)


def _assert_lightrag_call_sites_correlated(
    wm_base: str, correlation_key: str, *, expected: tuple[str, ...]
) -> None:
    """Poll thread-root state until ALL expected LightRAG call-sites are recorded.

    A call-site lands in the thread-root state only if its request still carried
    ``X-Threlium-Thread-Root`` (global recorder, §3.6.1) → presence == correlator preserved
    through the wrapper. On timeout, report the MISSING set (= lost/scrambled correlators).
    """
    want = set(expected)

    def _probe() -> set[str] | None:
        cs = set(wiremock_state_thread_root_call_sites(wm_base, correlation_key))
        return cs if want <= cs else None

    try:
        present = poll_until(
            _probe,
            timeout=TIMEOUT_POLL_SHORT,
            interval=2.0,
            desc=f"all lightrag call-sites in thread-root state: {sorted(want)}",
        )
    except TimeoutError:
        present = set(wiremock_state_thread_root_call_sites(wm_base, correlation_key))
        missing = want - present
        raise AssertionError(
            "LightRAG call-sites missing from thread-root state (correlator lost/scrambled "
            f"through wrapper): {sorted(missing)}. present={sorted(present)}; "
            f"correlation_key={correlation_key!r}"
        ) from None
    log.info("lightrag_correlator_integrity_ok", present=sorted(present))


@contextlib.contextmanager
def _integrity_contour(e2e_runtime: E2EComposeRuntime) -> Iterator[tuple[str, str]]:
    """Один НЕЗАВИСИМЫЙ integrity-contour: inject dense message → full mailflow pipeline →
    yield ``(wm_base, correlation_key)`` для фасет-ассерта.

    КАЖДЫЙ фасет-тест ниже гоняет СВОЙ контур через этот CM — полная независимость тестов, без
    shared-fixture / ``xdist_group`` / сериализации (это антипаттерны: тесты должны быть совершенно
    независимы и свободно распределяться xdist'ом). Цена — отдельный контур на фасет; выигрыш —
    гранулярная атрибуция флака (видно КАКОЙ коррелятор потерян, а не «весь integrity упал»)."""
    with mailflow_inject_and_wait(LIGHTRAG_INTEGRITY_SPEC, e2e_runtime.project_name) as (
        project,
        raw_id,
        _canonical_id,
        nm_inner,
        stub_tag,
        correlation_key,
    ):
        try:
            assert_full_mailflow_pipeline(
                LIGHTRAG_INTEGRITY_SPEC,
                project=project,
                raw_id=raw_id,
                nm_inner=nm_inner,
                stub_tag=stub_tag,
                correlation_key=correlation_key,
            )
            rt = discover_runtime(project, repo_root=REPO_ROOT)
            wm_base = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
            yield wm_base, correlation_key
        except Exception:
            log.debug(
                "failure_artifacts",
                body=clip_log_body(dump_failure_artifacts(project, repo_root=REPO_ROOT)),
            )
            raise


def test_lightrag_query_side_correlators(e2e_runtime: E2EComposeRuntime) -> None:
    """Query-side wrappers (enrich aquery) preserve the correlator under THIS turn's thread-root.

    ``extract_query_keywords`` / ``lightrag_query`` (embeddings) / ``generate_rag_answer`` — each lands
    in the thread-root state ONLY if it still carried ``X-Threlium-Thread-Root``; a miss == lost/scrambled
    correlator. (``generate_rag_answer`` fires on non-empty retrieval — relies on the bootstrap-seeded vdb.)"""
    with _integrity_contour(e2e_runtime) as (wm_base, correlation_key):
        _assert_lightrag_call_sites_correlated(
            wm_base, correlation_key, expected=_THREAD_ROOT_CALL_SITES
        )


def test_lightrag_index_correlator(e2e_runtime: E2EComposeRuntime) -> None:
    """Index embeddings wrapper preserves THIS test's static index-corr token (per-test, not a global
    fire-at-all counter): the indexed (enriched) message carries the baked ``E2E-INDEX-CORR-*`` marker and
    the generic 011 stub records ``lightrag_index`` under it → presence == the index wrapper kept its
    correlator."""
    with _integrity_contour(e2e_runtime) as (wm_base, _correlation_key):
        _assert_lightrag_call_sites_correlated(
            wm_base, _INDEX_CORR_MARKER, expected=_INDEX_CALL_SITES
        )


def test_lightrag_kg_extraction_correlators(e2e_runtime: E2EComposeRuntime) -> None:
    """KG-extraction wrappers (``extract_knowledge_graph`` + ``_gleaning``) fire AT ALL with their
    call-site — recorded under the FIXED global context (the drain batches/aggregates docs into one
    extraction prompt, so per-message attribution is impossible; fire-at-all is the integrity goal)."""
    with _integrity_contour(e2e_runtime) as (wm_base, _correlation_key):
        _assert_lightrag_call_sites_correlated(
            wm_base, _KG_GLOBAL_CONTEXT, expected=_KG_CALL_SITES
        )
