"""Цепочка предков только по ``In-Reply-To`` (лист → корень).

Общий примитив для :mod:`threlium.ingress_route_resolve` и enrich-контекста.

**Материализация:** :func:`iter_in_reply_to_ancestors_from_inner_id` возвращает
``list[IrtAncestorSnapshot]`` — иммутабельные снимки, снятые под одним коротким
read-сеансом notmuch. Курсор Xapian закрывается до начала тяжёлой бизнес-логики;
``notmuch2.Message`` не утекает за пределы ``with notmuch_database``.
"""
from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from contextvars import ContextVar

import notmuch2  # pyright: ignore[reportMissingImports]

from threlium import nm
from threlium.logutil import logger

log = logger.bind(component=__name__)
from threlium.types import (
    NotmuchMessageIdInner,
    RfcInReplyToWire,
)


# VO снимка вынесен в ``types/notmuch_snapshot.py`` (чтобы им мог пользоваться и ``nm``-уровень, и
# ``types/ingress_hitl`` без import-цикла); здесь — ре-экспорт для прежних потребителей ``irt_chain``.
from threlium.types.notmuch_snapshot import (  # noqa: E402
    IrtAncestorSnapshot,
    IrtSubagentMarker,
    snapshot_from_nm_message,
)


def _require_matching_indexed_mid(
    nm_msg: notmuch2.Message, expected: NotmuchMessageIdInner
) -> NotmuchMessageIdInner:
    indexed = nm.require_inner_message_id_from_notmuch_message(nm_msg)
    if not indexed.equals_case_insensitive(expected):
        raise RuntimeError(
            "notmuch Message-ID не согласован с цепочкой конверта/In-Reply-To: "
            f"index={indexed.value!r} expected={expected.value!r} path={nm_msg.path!r}"
        )
    return indexed


def _next_parent_inner_raw(irt_w: RfcInReplyToWire | None) -> NotmuchMessageIdInner | None:
    return NotmuchMessageIdInner.from_optional_raw(
        irt_w.value if irt_w is not None else None
    )


def _leaf_not_in_index_msg(inner: NotmuchMessageIdInner) -> str:
    return (
        "FSM-инвариант: лист не найден в union notmuch по inner Message-ID "
        f"(Message-ID={inner.as_angle_bracket_header()})"
    )


def _parent_missing_msg(parent: NotmuchMessageIdInner) -> str:
    return (
        "FSM-инвариант: разрыв IRT-цепочки — предок объявлен в In-Reply-To, "
        "но отсутствует в индексе "
        f"(Message-ID={parent.as_angle_bracket_header()})"
    )


def _materialize_one_ancestor(
    db: notmuch2.Database,
    expected: NotmuchMessageIdInner,
    *,
    is_leaf: bool,
    start_inner: NotmuchMessageIdInner,
    seen_inner: set[str],
) -> IrtAncestorSnapshot:
    """Прочитать РОВНО одного предка под открытым read ``db`` в иммутабельный снимок.

    ``RuntimeError`` — нарушение FSM-инварианта (нет листа/предка, цикл): НЕ ретраится.
    ``notmuch2.XapianError`` / ``NullPointerError`` (discard ревизии под конкурентной записью) может
    прилететь из любого CFFI-чтения; вызывающий переоткрывает БД и продолжает с ТОГО ЖЕ ``expected``.
    Контракт resume: ``seen_inner`` пополняется ТОЛЬКО после полностью успешного снимка — иначе после
    discard'а на чтении заголовков ключ уже лежал бы в ``seen_inner`` и resume ложно сообщил бы о цикле."""
    nm_msg = nm.first_notmuch_message_for_inner_id(db, expected)
    if nm_msg is None:
        raise RuntimeError(
            _leaf_not_in_index_msg(start_inner) if is_leaf else _parent_missing_msg(expected)
        )
    indexed = _require_matching_indexed_mid(nm_msg, expected)
    key = indexed.value.casefold()
    if key in seen_inner:
        raise RuntimeError(
            "FSM-инвариант: цикл в цепочке In-Reply-To "
            f"(Message-ID={indexed.as_angle_bracket_header()})"
        )
    snap = snapshot_from_nm_message(nm_msg, indexed)
    seen_inner.add(key)  # commit ТОЛЬКО после полного снимка (resume-safe, см. docstring)
    return snap


def _drain_irt_frontier(
    db: notmuch2.Database,
    start_inner: NotmuchMessageIdInner,
    frontier: NotmuchMessageIdInner | None,
    result: list[IrtAncestorSnapshot],
    seen_inner: set[str],
) -> None:
    """Продолжить обход лист → корень с ``frontier`` под открытым read ``db``; снимки — в ``result`` на месте.

    Возврат — когда достигнут корень (``parent is None``). При discard'е CFFI-чтения частичные
    ``result`` / ``seen_inner`` остаются согласованными (мутируются лишь ПОСЛЕ полного снимка каждого
    предка), поэтому вызывающий переоткрывает БД и зовёт снова — frontier выводится из ``result[-1]``."""
    next_inner = frontier
    while next_inner is not None:
        snap = _materialize_one_ancestor(
            db,
            next_inner,
            is_leaf=(len(result) == 0),
            start_inner=start_inner,
            seen_inner=seen_inner,
        )
        result.append(snap)
        next_inner = _next_parent_inner_raw(snap.header_in_reply_to)


# Кэш материализации IRT-цепочки на ВРЕМЯ одной FSM-стадии (per-message). Одна стадия обходит цепочку
# МНОГО раз (enrich_context — 5 мест, task/collect, route-resolve, subagent-classifier, response/collect,
# formal_reason_gate, enrich_fast…), каждый обход заново открывает notmuch и читает N предков × ~6
# заголовков — это была горячая GIL-точка handler-тредов под параллельностью (py-spy). Цепочка иммутабельна
# в пределах синхронной обработки стадии, поэтому материализуем РОВНО раз на ``start_inner``, остальное —
# из кэша. Активируется :func:`stage_materialization_cache` в ``fsm._run_stage``; вне scope (нет ctx) —
# поведение прежнее (каждый раз свежий обход).
#
# Тот же scope бесшовно покрывает и ЛЕНИВЫЙ разбор тел: :attr:`IrtAncestorSnapshot.email_message`
# мемоизируется на самих снимках (идентичность снимков стабильна, пока жив этот кэш), поэтому отдельного
# keyspace под распарсенные ``EmailMessage`` не нужно — единый механизм.
_IRT_CHAIN_CACHE: ContextVar[dict[str, list[IrtAncestorSnapshot]] | None] = ContextVar(
    "_irt_chain_cache", default=None
)


@contextlib.contextmanager
def stage_materialization_cache() -> Iterator[None]:
    """Единый scope per-stage материализации на одну FSM-стадию (см. ``_IRT_CHAIN_CACHE``).

    Покрывает (а) кэш обхода IRT-цепочки и (б) lazy-мемо распарсенных тел писем на снимках
    (:attr:`IrtAncestorSnapshot.email_message`) — один механизм, один жизненный цикл."""
    token = _IRT_CHAIN_CACHE.set({})
    try:
        yield
    finally:
        _IRT_CHAIN_CACHE.reset(token)


_IRT_RESUME_MAX_NOPROGRESS = 5
_IRT_RESUME_WAIT_MIN = 0.05
_IRT_RESUME_WAIT_MAX = 2.0


def _materialize_irt_chain_session(
    start_inner: NotmuchMessageIdInner,
) -> list[IrtAncestorSnapshot]:
    """Открыть read-БД, материализовать IRT-цепочку в иммутабельные снимки. **RESUMABLE.**

    При discard'е ревизии под конкурентной записью (``XapianError`` / ``NullPointerError``, см.
    :func:`nm._is_concurrent_revision_discard`) переоткрываем БД и ПРОДОЛЖАЕМ обход с предка, на котором
    упали — уже собранные frozen-снимки валидны (данные скопированы) и сохраняются. Прежний
    ``@nm.read_retry`` рестартил весь обход с нуля на каждый discard: под ``-n12`` тяжёлая запись →
    частые discard'ы → перечитывание O(depth) тришило (профиль: ~300 ретраев/прогон). Теперь discard
    стоит ОДНО переоткрытие + дочитку хвоста, а не всю цепочку. Наружу — только иммутабельные снимки;
    ни один ``notmuch2.Message`` не покидает сеанс.

    Cap — по ПОДРЯД идущим неуспехам БЕЗ прогресса (один и тот же предок discard'ится раз за разом):
    как только глубина выросла, счётчик сбрасывается, поэтому глубокая цепочка под нагрузкой не
    исчерпает лимит, пока двигается вперёд."""
    result: list[IrtAncestorSnapshot] = []
    seen_inner: set[str] = set()
    noprogress = 0
    last_depth = 0
    while True:
        frontier = (
            _next_parent_inner_raw(result[-1].header_in_reply_to) if result else start_inner
        )
        if frontier is None:
            return result  # корень достигнут
        try:
            with nm.notmuch_database(write=False) as db:
                _drain_irt_frontier(db, start_inner, frontier, result, seen_inner)
            return result
        except Exception as exc:
            # Только discard (вкл. голый cffi-NULL RuntimeError от ``msg.path``, см.
            # ``nm.is_concurrent_revision_discard``) → resume; FSM-инвариант (нет листа/предка, цикл)
            # — это тоже RuntimeError, но НЕ discard → пробрасываем.
            if not nm.is_concurrent_revision_discard(exc):
                raise
            if len(result) > last_depth:  # был прогресс → сбросить no-progress счётчик
                last_depth = len(result)
                noprogress = 0
            noprogress += 1
            if noprogress >= _IRT_RESUME_MAX_NOPROGRESS:
                log.warning(
                    "irt_chain_materialize_exhausted",
                    depth=len(result),
                    frontier=frontier.value,
                    noprogress=noprogress,
                    err=type(exc).__name__,
                )
                raise
            wait = min(_IRT_RESUME_WAIT_MAX, _IRT_RESUME_WAIT_MIN * (2 ** (noprogress - 1)))
            log.warning(
                "irt_chain_materialize_resume",
                depth=len(result),
                frontier=frontier.value,
                noprogress=noprogress,
                err=type(exc).__name__,
                wait_s=round(wait, 3),
            )
            time.sleep(wait)


def iter_in_reply_to_ancestors_from_inner_id(
    start_inner: NotmuchMessageIdInner,
) -> list[IrtAncestorSnapshot]:
    """Лист → корень: иммутабельные ``IrtAncestorSnapshot``; дальше — только VO (``docs/TYPES.md`` «границы
    API», ``docs/THREAD_MODEL.md`` §3).

    В активном :func:`stage_materialization_cache`-scope (FSM-стадия) повторные вызовы с тем же
    ``start_inner`` возвращают уже материализованную цепочку из кэша (снимки иммутабельны → переиспользование
    безопасно); вне scope — каждый раз свежий notmuch-обход."""
    cache = _IRT_CHAIN_CACHE.get()
    if cache is None:
        return _materialize_irt_chain_session(start_inner)
    key = start_inner.value.casefold()
    hit = cache.get(key)
    if hit is None:
        hit = _materialize_irt_chain_session(start_inner)
        cache[key] = hit
    return hit

