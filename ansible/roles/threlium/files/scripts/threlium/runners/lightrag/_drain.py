"""Drain / Sweep scheduling: collect pending → ainsert → tag → self-schedule."""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from lightrag import LightRAG

from threlium.litellm_correlation_headers import build_litellm_correlation_headers_from_notmuch
from threlium.litellm_route_context import (
    e2e_route_wire_tail,
    reset_litellm_correlation_ctxvar,
    set_litellm_correlation_ctxvar,
)
from threlium.lightrag_drain_query import lightrag_drain_pending_search
from threlium.logutil import logger
from threlium.mail import email_message_from_path
from threlium.mime_reform import message_has_history
from threlium.lightrag_ingest import render_lightrag_ingest_document
from threlium.nm import (
    batch_tag_add,
    notmuch_database,
    read_retry,
    require_inner_message_id_from_notmuch_message,
)
from threlium.settings import (
    ThreliumSettings,
    resolve_llm_endpoint,
)
from threlium.systemd_notify import notify_status
from threlium.types import (
    FsmStage,
    LightragDrainSkipReason,
    LitellmCallSite,
    LitellmCorrelationSnapshot,
    LitellmRoutingSite,
    NotmuchMessageIdInner,
    NotmuchMessageIds,
    NotmuchQueryField,
    NotmuchTag,
    NotmuchThreadScopeId,
)
from threlium.types.systemd_status import SystemdStatusBody

log = logger.bind(stage="lightrag")

_drain_task: asyncio.Task[None] | None = None
# Выставляется на shutdown: drain перестаёт self-schedule'ить НОВЫЕ батчи (in-flight доходит сам).
_drain_quiesce: bool = False


def reset_drain_task() -> None:
    """Reset drain task state (called during lifecycle cleanup)."""
    global _drain_task, _drain_quiesce
    _drain_task = None
    _drain_quiesce = False


def request_drain_quiesce() -> None:
    """Shutdown: остановить self-schedule НОВЫХ drain-батчей. In-flight ``drain_single_batch`` (его
    ``ainsert`` → lancedb ``merge_insert``) доходит до конца сам — новый батч не создаётся."""
    global _drain_quiesce
    _drain_quiesce = True


async def await_drain_idle(timeout: float) -> bool:
    """Дождаться завершения in-flight drain-батча (его ``ainsert`` → запись lancedb) ДО ``finalize_storages``.

    Иначе blanket-cancel на shutdown прерывает ``merge_insert`` ПОСРЕДИ записи, а ``finalize_storages``
    коммитит полузаписанный стор → манифест ссылается на нефлашнутый data-файл (``LanceError(IO): Not
    found …data/*.lance``), и порча переживает рестарт, отравляя весь следующий прогон. ``shield`` — НЕ
    отменять ainsert по таймауту (отмена посреди merge_insert = та самая порча). ``True`` = drain idle в
    пределах ``timeout`` (зови ПОСЛЕ :func:`request_drain_quiesce`, пока worker-пулы ещё живы)."""
    t = _drain_task
    if t is None or t.done():
        return True
    try:
        await asyncio.wait_for(asyncio.shield(t), timeout=timeout)
    except asyncio.TimeoutError as exc:
        log.warning("await_drain_idle_timeout", timeout_sec=timeout, exc_info=exc)
        return False
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        log.warning("await_drain_idle_batch_failed", exc_info=exc)
        return True  # батч упал сам (drain_batch_failed) — это не in-flight lancedb write
    return True


def _lightrag_doc_id(mid_inner: NotmuchMessageIdInner) -> str:
    """Короткий стабильный lightrag-doc-id из inner-MID.

    Milvus-схема lightrag (`milvus_impl.py`) задаёт поле ``id`` как ``VARCHAR(64)``; threlium-MID
    (base62, ~84–108) + chunk-суффикс lightrag даёт chunk-id >64 → ``MilvusException code=6 value
    length exceeds max_length=64`` → insert отвергнут, pipeline halted, письмо не индексируется
    (faiss длину не ограничивал). Это лишь dedup-ключ lightrag (трекинг notmuch — отдельно, через
    ``tag_ids``/``LIGHTRAG_INDEXED``), не user-facing; sha1 детерминирован → ``doc_status`` dedup и
    idempotency-bootstrap целы. ``th-`` + 40 hex = 43 симв. → chunk-id ~52 < 64.
    """
    return "th-" + hashlib.sha1(mid_inner.value.encode("utf-8")).hexdigest()


def _future_timeout_sec(settings: ThreliumSettings) -> float | None:
    llm_ep = resolve_llm_endpoint(settings.litellm, LitellmRoutingSite.LIGHTRAG_LLM)
    v = float(llm_ep.timeout)
    return v if v > 0 else None


def _effective_batch_size(settings: ThreliumSettings) -> int:
    # Индексация развязана от тестов (enrich-барьер в mailflow assert), поэтому батч из settings
    # и в e2e — индексация async background, тесты не ждут drain.
    return max(1, settings.lightrag.insert_batch)


@dataclass(frozen=True)
class _DrainPendingItem:
    """Иммутабельный снимок pending-письма drain'а — материализуется РАЗ в collect-проходе.

    Снимок-подход (как :class:`~threlium.irt_chain.IrtAncestorSnapshot`): живой ``notmuch2.Message``
    не покидает открытый ``with notmuch_database`` — наружу только frozen-VO. Корреляция снимается в
    ТОМ ЖЕ сеансе, что и письмо (без второго ``db.get`` живого Message)."""

    path: Path
    message_id_inner: NotmuchMessageIdInner
    thread_scope: NotmuchThreadScopeId | None
    correlation: "LitellmCorrelationSnapshot | None"


@read_retry
def _collect_batch(limit: int, *, with_correlation: bool) -> list["_DrainPendingItem"]:
    """pending-снимки[…limit] под ОДНОЙ READ-транзакцией → только VO наружу.

    ``@read_retry``: при discard'е ревизии под конкурентной записью сеанс переоткрывается (rag-loop
    в движке многопоточен; ``notmuch2.Message`` не покидает ``with``). При ``with_correlation`` снимок
    корреляции индексатора собирается ЗДЕСЬ ЖЕ (антипаттерн «живой Message наружу / повторный db.get»
    устранён)."""
    out: list[_DrainPendingItem] = []
    selector = lightrag_drain_pending_search()
    with notmuch_database(write=False) as db:
        for msg in db.messages(selector):
            fp = Path(msg.path)
            if not fp.is_file():
                continue
            ids = NotmuchMessageIds.from_notmuch(msg)
            mid_inner = require_inner_message_id_from_notmuch_message(msg)
            corr = (
                LitellmCorrelationSnapshot.from_mapping(
                    build_litellm_correlation_headers_from_notmuch(
                        db, msg, call_site=LitellmCallSite.LIGHTRAG_INDEX
                    )
                )
                if with_correlation
                else None
            )
            out.append(_DrainPendingItem(fp, mid_inner, ids.threadid, corr))
            if len(out) >= limit:
                break
    return out


async def _ainsert_with_correlation(
    rag: LightRAG,
    texts: list[str],
    ids: list[str],
    file_paths: list[str],
    correlation: "LitellmCorrelationSnapshot",
    settings: ThreliumSettings,
) -> float:
    """ainsert с e2e-correlation ctxvar. Снимок собран в collect-проходе, не повторным ``db.get``."""
    log.debug(
        "drain_e2e_ainsert",
        batch_size=len(ids),
        route_tail=e2e_route_wire_tail(correlation.route_wire),
        call_site=correlation.call_site,
        first_mid=ids[0],
    )
    # X-Threlium-Thread-Root на индексаторе НЕ ставится (build_litellm_correlation_headers_from_notmuch):
    # под конкуренцией пула thread-root misattributed; per-document коррелятор = Message-ID в теле чанка
    # (body-corr, E2E.md §3.6.3). БЕЗ index↔query барьера — RAG eventual-consistent.
    token = set_litellm_correlation_ctxvar(correlation.as_dict())
    try:
        t0 = time.monotonic()
        await rag.ainsert(texts, ids=ids, file_paths=file_paths)
        return time.monotonic() - t0
    finally:
        reset_litellm_correlation_ctxvar(token)


async def _ainsert_plain(
    rag: LightRAG, texts: list[str], ids: list[str], file_paths: list[str]
) -> float:
    """ainsert without correlation. Returns elapsed seconds."""
    t0 = time.monotonic()
    await rag.ainsert(texts, ids=ids, file_paths=file_paths)
    return time.monotonic() - t0


async def _ainsert_batch(
    rag: LightRAG,
    pending: list["_DrainPendingItem"],
    settings: ThreliumSettings,
) -> None:
    """Render pending messages → ainsert → tag as indexed."""
    llm_timeout = _future_timeout_sec(settings)

    texts: list[str] = []
    ids: list[str] = []
    tag_ids: list[NotmuchMessageIdInner] = []
    file_paths: list[str] = []
    correlations: list["LitellmCorrelationSnapshot | None"] = []
    skip_tag_ids: list[NotmuchMessageIdInner] = []

    for item in pending:
        fp, mid_inner, tid = item.path, item.message_id_inner, item.thread_scope
        try:
            msg = email_message_from_path(fp)
        except Exception as exc:
            log.error(
                "index_skip",
                reason=LightragDrainSkipReason.RENDER_FAILED.value,
                path=str(fp),
                exc_type=type(exc).__name__,
                exc_msg=str(exc),
            )
            skip_tag_ids.append(mid_inner)
            continue
        # Финальный предикат содержательности (selector даёт лишь tag-негативы): письмо
        # достойно графа, только если несёт ``<history>``-часть. Системные/control-письма
        # (только ``<system>``, без history) индексировать нечем — помечаем skipped, чтобы
        # не оставлять их в вечном pending.
        if not message_has_history(msg):
            to_stage = FsmStage.try_from_incoming_to(msg)
            log.info(
                "index_skip",
                reason=LightragDrainSkipReason.NO_HISTORY.value,
                path=str(fp),
                to_stage=to_stage.value if to_stage is not None else None,
            )
            skip_tag_ids.append(mid_inner)
            continue
        try:
            thread_term = (
                tid.as_notmuch_thread_term()
                if tid is not None
                else NotmuchQueryField.THREAD.term("unknown")
            )
            text = render_lightrag_ingest_document(msg, thread_term=thread_term)
        except Exception as exc:
            log.error(
                "index_skip",
                reason=LightragDrainSkipReason.RENDER_FAILED.value,
                path=str(fp),
                exc_type=type(exc).__name__,
                exc_msg=str(exc),
            )
            skip_tag_ids.append(mid_inner)
            continue
        texts.append(text)
        ids.append(_lightrag_doc_id(mid_inner))
        tag_ids.append(mid_inner)
        file_paths.append(str(fp))
        correlations.append(item.correlation)

    if skip_tag_ids:
        skipped_tagged = batch_tag_add(skip_tag_ids, NotmuchTag.LIGHTRAG_SKIPPED)
        log.warning(
            "index_skipped_tagged",
            count=len(skip_tag_ids),
            tagged=skipped_tagged,
        )

    if not texts:
        if not skip_tag_ids:
            raise RuntimeError(
                "lightrag: pending batch produced no texts "
                f"(paths={[str(it.path) for it in pending]!r})"
            )
        return

    lead_correlation = correlations[0]
    if settings.e2e.litellm_route_correlation and lead_correlation is not None:
        elapsed = await _ainsert_with_correlation(
            rag, texts, ids, file_paths, lead_correlation, settings
        )
    else:
        elapsed = await _ainsert_plain(rag, texts, ids, file_paths)

    if llm_timeout is not None and elapsed > 0.8 * llm_timeout:
        log.warning("ainsert_slow", elapsed_sec=round(elapsed, 1), llm_timeout_sec=llm_timeout)
    elif elapsed > 60:
        log.info("ainsert_elapsed", elapsed_sec=round(elapsed, 1), batch_size=len(ids))

    tagged = batch_tag_add(tag_ids, NotmuchTag.LIGHTRAG_INDEXED)
    log.info("ainsert_complete", docs=len(ids), tagged=tagged)
    notify_status(SystemdStatusBody.lightrag_idle_indexed(message_count=len(ids)))


async def drain_single_batch(
    rag: LightRAG, settings: ThreliumSettings, lock: asyncio.Lock
) -> None:
    """One batch: collect → ainsert → tag → sweep (self-schedule if more pending).

    Паттерн sweep (аналог threlium-work@ → OnSuccess → threlium-sweep@):
    задача обрабатывает один батч, после завершения проверяет backlog и
    при наличии pending создаёт следующую задачу.
    """
    global _drain_task
    batch_size = _effective_batch_size(settings)

    try:
        async with lock:
            pending = _collect_batch(
                batch_size, with_correlation=settings.e2e.litellm_route_correlation
            )
            if not pending:
                notify_status(SystemdStatusBody.lightrag_idle_no_pending())
                return
            notify_status(SystemdStatusBody.lightrag_indexing_batch(batch_size=len(pending)))
            await _ainsert_batch(rag, pending, settings)
    except asyncio.CancelledError as exc:
        log.debug("drain_batch_cancelled", exc_info=exc)
        return
    except BaseException as ex:
        log.error("drain_batch_failed", exc_info=ex)
        raise

    # Sweep-проба «есть ли ещё pending» — корреляция не нужна (только truthiness). На shutdown
    # (_drain_quiesce) НОВЫЙ батч не создаём: цепочка останавливается, await_drain_idle дождётся этого.
    if not _drain_quiesce and _collect_batch(1, with_correlation=False):
        _drain_task = asyncio.create_task(
            drain_single_batch(rag, settings, lock)
        )


def schedule_on_loop(rag: LightRAG, settings: ThreliumSettings, lock: asyncio.Lock) -> None:
    """Create a drain task if none is running (called via loop.call_soon_threadsafe).

    Singleton-гард СОХРАНЁН: конкурентные drain-задачи интерливят ainsert через общий
    frozen-pool lightrag → per-call корреляция теряется (embeddings без X-Threlium-Call-Site →
    unmatched). Одна drain-цепочка за раз = корреляция сохраняется. Разлок index↔query даёт
    no-op ``_drain_lock`` (drain не блокирует aquery); singleton сериализует лишь drain↔drain.
    """
    global _drain_task
    if _drain_task is not None and not _drain_task.done():
        return
    _drain_task = asyncio.create_task(
        drain_single_batch(rag, settings, lock)
    )
