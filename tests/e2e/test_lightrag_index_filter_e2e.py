"""Селективная индексация LightRAG на **уже поднятом** e2e-стеке (без compose/bake/Ansible).

**Тест-кейс.** Полный почтовый контур (как ``test_mailflow_e2e``) прогоняется до
конца, после чего проверяется **симметрия отбора enrich/drain**: drain индексирует
только content-indexable стадии и отсекает SERVICE/не-whitelist стадии на уровне
notmuch-селектора (push-down), а не пост-фильтром в Python.

Инвариант (см. [`INDEX.md` §5b.2](../../docs/INDEX.md), [`TYPES.md`](../../docs/TYPES.md)):

* ``to:ingress@localhost`` (content-indexable, роль ``USER_INPUT``) → ``tag:lightrag_indexed``;
* ``to:enrich@localhost`` (``SERVICE``), ``to:reasoning@localhost`` (нет роли),
  ``to:response_finalize@localhost`` (нет роли) — в треде присутствуют, но **не**
  ``tag:lightrag_indexed``: селектор ``lightrag_drain_pending_search()`` их не выбрал;
* drain доходит до idle — отсечённые письма не «застревают» в pending.

Принципиальное отличие от enrich: enrich собирает контекст обходом IRT-цепочки
(`to_stage_in_unified_role`, memory — отдельные бакеты), drain сканирует union-индекс
глобально (`content_indexable_stages`, memory индексируется напрямую). Поэтому единый
базис ``CONTEXT_ROLE_BY_TO_STAGE`` (роль ≠ SERVICE), но два предиката.

Стабы WireMock переиспользуются из ``wiremock_stubs/test_mailflow_e2e/`` — отдельная
изоляция по ``X-Threlium-Thread-Root`` (уникальный ``correlation_key``).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.log import clip_log_body, log
from threlium.types import FsmStage

from .helpers import (
    MailflowScenarioSpec,
    REPO_ROOT,
    assert_full_mailflow_pipeline,
    assert_notmuch_thread_lightrag_index_filter,
    dump_failure_artifacts,
    mailflow_inject_and_wait,
)

_WIREMOCK_STUBS_ROOT = Path(__file__).resolve().parent / "wiremock_stubs"

LIGHTRAG_FILTER_SPEC = MailflowScenarioSpec(
    label="lightrag_index_filter_e2e",
    raw_id_prefix="lrf-ing-",
    stub_dir=_WIREMOCK_STUBS_ROOT / "test_mailflow_e2e",
    stub_tag="stub-mailflow-e2e-01",
    body_head="e2e index filter body",
    min_chat_completion_posts=2,
    min_embedding_posts=5,
)

_INDEXED_STAGES: tuple[FsmStage, ...] = (FsmStage.INGRESS,)
_EXCLUDED_STAGES: tuple[FsmStage, ...] = (
    FsmStage.ENRICH,
    FsmStage.REASONING,
    FsmStage.RESPONSE_FINALIZE,
)


@pytest.fixture()
def lightrag_filter_stack(deployed_stack: str) -> object:
    with mailflow_inject_and_wait(LIGHTRAG_FILTER_SPEC, deployed_stack) as ids:
        yield ids


@pytest.mark.e2e
@pytest.mark.e2e_live
@pytest.mark.mailflow
def test_lightrag_selective_indexing(
    lightrag_filter_stack: tuple[str, str, str, str, str, str],
) -> None:
    """Drain индексирует content-indexable стадии и отсекает SERVICE/не-whitelist."""
    project, raw_id, _canonical_id, nm_inner, stub_tag, correlation_key = (
        lightrag_filter_stack
    )
    try:
        assert_full_mailflow_pipeline(
            LIGHTRAG_FILTER_SPEC,
            project=project,
            raw_id=raw_id,
            nm_inner=nm_inner,
            stub_tag=stub_tag,
            correlation_key=correlation_key,
        )
        assert_notmuch_thread_lightrag_index_filter(
            project,
            anchor_message_id=nm_inner,
            indexed_stages=_INDEXED_STAGES,
            excluded_stages=_EXCLUDED_STAGES,
            repo_root=REPO_ROOT,
        )
    except Exception:
        log.debug(
            "failure_artifacts",
            body=clip_log_body(dump_failure_artifacts(project, repo_root=REPO_ROOT)),
        )
        raise
