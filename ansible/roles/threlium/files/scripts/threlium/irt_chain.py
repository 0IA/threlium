"""Цепочка предков только по ``In-Reply-To`` (лист → корень).

Общий примитив для :mod:`threlium.ingress_route_resolve` и enrich-контекста.

**Материализация:** :func:`iter_in_reply_to_ancestors_from_inner_id` возвращает
``list[IrtAncestorSnapshot]`` — иммутабельные снимки, снятые под одним коротким
read-сеансом notmuch. Курсор Xapian закрывается до начала тяжёлой бизнес-логики;
``notmuch2.Message`` не утекает за пределы ``with notmuch_database``.
"""
from __future__ import annotations

import contextlib
import enum
import functools
import time
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path

import notmuch2  # pyright: ignore[reportMissingImports]

from threlium import nm
from threlium.logutil import logger

log = logger.bind(component=__name__)
from threlium.types import (
    FsmStage,
    IngressRouteB62Wire,
    MailHeaderName,
    NotmuchMessageIdInner,
    RfcFromWire,
    RfcInReplyToWire,
    RfcReferencesWire,
    RfcSubjectWire,
    RfcToWire,
)


class IrtSubagentMarker(enum.Enum):
    """Маркер субагента на одном снимке IRT — единая точка классификации.

    Баланс маркеров (``SUBAGENT_INTENT`` +1 / ``SUBAGENT_END`` −1) — общее ядро
    для обхода с фрейм-изоляцией (:mod:`threlium.thread_context_filter`) и расчёта
    глубины/родителя (:mod:`threlium.irt_subagent_classifier`). Header-free: глубина
    выводится из линейной IRT-цепочки, а не из ``X-Threlium-Hop-Budget``.
    """

    PLAIN = "plain"
    SUBAGENT_INTENT = "subagent_intent"
    SUBAGENT_END = "subagent_end"


@dataclass(frozen=True)
class IrtAncestorSnapshot:
    """Иммутабельный снимок ``notmuch2.Message`` из обхода IRT-цепочки.

    Валиден после закрытия ``notmuch_database`` (все данные скопированы).
    """
    message_id_inner: NotmuchMessageIdInner
    path: Path
    tags: frozenset[str]
    header_from: RfcFromWire | None
    header_to: RfcToWire | None
    header_route: IngressRouteB62Wire | None
    header_references: RfcReferencesWire | None
    header_in_reply_to: RfcInReplyToWire | None
    header_subject: RfcSubjectWire | None

    @functools.cached_property
    def email_message(self) -> EmailMessage:
        """Полное распарсенное письмо этого предка — lazy, мемоизируется НА снимке.

        Снимок несёт лёгкие notmuch-заголовки (eager); тело MIME (``<task-init>`` /
        ``<system>`` / ``<history>``-части) живёт ТОЛЬКО в полном RFC822-разборе файла —
        это дорого (feedparser + structured-заголовки; py-spy: хотспот #1 GIL handler-
        тредов под параллельностью). Один обработчик обходит цепочку МНОГО раз
        (``collect_task_ops`` ×2, ``build_unified``, ``resolve_frame``, ``collect_ops`` …),
        каждый раз ре-парся те же файлы. Здесь парсим РОВНО раз на снимок: идентичность
        снимков стабильна в пределах стадии (кэш материализации цепочки —
        :func:`stage_materialization_cache`), поэтому повторные обращения к
        ``.email_message`` отдают готовый результат — единый механизм с lazy-парсом тел,
        без второго keyspace. ``cached_property`` пишет в ``__dict__`` напрямую, минуя
        frozen ``__setattr__``.

        КОНТРАКТ: результат ОБЩИЙ в пределах стадии — НЕ мутировать (как и сам frozen-
        снимок). Единственному мутатору (``build_unified_email_messages`` ``set_payload``)
        брать собственную копию (``canonicalize_mime``) перед изменением.
        """
        from threlium.mail import email_message_from_path

        return email_message_from_path(self.path)

    def subagent_marker(self) -> IrtSubagentMarker:
        """Маркер субагента на этом снимке (единая точка классификации для баланса).

        Используется как +1/−1 при header-free расчёте глубины/фрейма по IRT.
        """
        if self.is_sent_from_fsm_stage(FsmStage.SUBAGENT_END):
            return IrtSubagentMarker.SUBAGENT_END
        if self.is_sent_from_fsm_stage(FsmStage.SUBAGENT_INTENT):
            return IrtSubagentMarker.SUBAGENT_INTENT
        return IrtSubagentMarker.PLAIN

    def is_sent_from_fsm_stage(self, stage: FsmStage) -> bool:
        """Аналог ``nm_addressed.notmuch_message_sent_from_fsm_stage`` на снимке."""
        if self.header_from is None:
            return False
        want = stage.rfc822_mailbox.lower()
        for _, addr in getaddresses([self.header_from.value]):
            if addr and addr.strip().lower() == want:
                return True
        return False

    def is_addressed_to_fsm_stage(self, stage: FsmStage) -> bool:
        """Аналог ``nm_addressed.notmuch_message_addressed_to_fsm_stage`` на снимке."""
        if self.header_to is None:
            return False
        want = stage.rfc822_mailbox.lower()
        for _, addr in getaddresses([self.header_to.value]):
            if addr and addr.strip().lower() == want:
                return True
        return False

    def in_reply_to_inner(self) -> NotmuchMessageIdInner | None:
        """Распарсенный inner Message-ID из ``In-Reply-To`` (или ``None``)."""
        return NotmuchMessageIdInner.from_optional_raw(
            self.header_in_reply_to.value if self.header_in_reply_to is not None else None
        )

    def to_fsm_stage(self) -> FsmStage | None:
        """Стадия из ``To:`` снимка (``None`` если не ровно одна FSM-стадия @localhost)."""
        return FsmStage.try_from_to_header_value(
            self.header_to.value if self.header_to is not None else None
        )


def _snapshot_from_nm_message(nm_msg: notmuch2.Message, mid: NotmuchMessageIdInner) -> IrtAncestorSnapshot:
    return IrtAncestorSnapshot(
        message_id_inner=mid,
        path=Path(str(nm_msg.path)),
        tags=frozenset(nm_msg.tags),
        header_from=RfcFromWire.parse_present_from_nm_message(nm_msg, MailHeaderName.FROM.value),
        header_to=RfcToWire.parse_present_from_nm_message(nm_msg, MailHeaderName.TO.value),
        header_route=IngressRouteB62Wire.parse_present_from_nm_message(
            nm_msg, MailHeaderName.ROUTE.value
        ),
        header_references=RfcReferencesWire.parse_present_from_nm_message(
            nm_msg, MailHeaderName.REFERENCES.value
        ),
        header_in_reply_to=RfcInReplyToWire.parse_present_from_nm_message(
            nm_msg, MailHeaderName.IN_REPLY_TO.value
        ),
        header_subject=RfcSubjectWire.parse_present_from_nm_message(
            nm_msg, MailHeaderName.SUBJECT.value
        ),
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
    snap = _snapshot_from_nm_message(nm_msg, indexed)
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
        except (notmuch2.XapianError, notmuch2.NullPointerError) as exc:
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

