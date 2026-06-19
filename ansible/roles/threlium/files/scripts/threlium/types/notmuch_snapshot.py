"""Иммутабельный снимок notmuch-сообщения — VO-граница «живой ``notmuch2.Message`` → frozen snapshot».

Основа чистой архитектуры пайплайна: ``notmuch2.Message`` валиден только пока открыта его ``db`` (и его
ленивые курсоры — источник C++ ``DatabaseModifiedError`` под конкурентной записью, см.
``threlium.nm_reader_worker``). Поэтому на ГРАНИЦЕ (``db.find`` → живой msg) сразу снимаем ВСЕ нужные поля
в этот иммутабельный VO (``snapshot_from_nm_message``), а вся бизнес-логика работает на снимке — живой
Message за пределы открытого ``with notmuch_database`` не утекает и в бизнес-функции не передаётся.

Раньше жил в ``threlium.irt_chain`` (откуда и имя ``IrtAncestor*``); вынесен в ``types/`` как VO, чтобы им
могли пользоваться и ``nm``-уровень, и ``types/ingress_hitl`` без import-цикла (``irt_chain`` импортирует
``nm``). ``irt_chain`` ре-экспортирует эти имена для совместимости.
"""
from __future__ import annotations

import enum
import functools
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path

import notmuch2  # pyright: ignore[reportMissingImports]

from threlium.types import (
    FsmStage,
    IngressRouteB62Wire,
    MailHeaderName,
    NotmuchMessageIdInner,
    NotmuchThreadScopeId,
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
    thread_scope: NotmuchThreadScopeId  # инвариант notmuch: всегда present (см. require_from_notmuch_thread_attr)

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
        from threlium.mail import email_message_from_maildir

        # ``self.path`` заморожен на материализации и может устареть от ``nm_settle`` (атомарный
        # ``new/``→``cur/``); резолвим живой файл по неизменному base в МОМЕНТ чтения, а кэшируем
        # РЕЗУЛЬТАТ (контент) — этот ``cached_property``, НЕ путь.
        return email_message_from_maildir(self.path)

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
        """True, если ``From:`` снимка содержит канонический mailbox стадии (на снимке, не на Message)."""
        if self.header_from is None:
            return False
        want = stage.rfc822_mailbox.lower()
        for _, addr in getaddresses([self.header_from.value]):
            if addr and addr.strip().lower() == want:
                return True
        return False

    def is_addressed_to_fsm_stage(self, stage: FsmStage) -> bool:
        """True, если среди ``To:`` снимка есть канонический mailbox стадии (на снимке, не на Message)."""
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


def snapshot_from_nm_message(nm_msg: notmuch2.Message, mid: NotmuchMessageIdInner) -> IrtAncestorSnapshot:
    """ГРАНИЦА: живой ``notmuch2.Message`` (под открытой ``db``) → иммутабельный снимок (все поля сняты).

    Единственное место, читающее поля с живого Message для снимка; дальше работают только со снимком."""
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
        thread_scope=NotmuchThreadScopeId.require_from_notmuch_thread_attr(nm_msg.threadid),
    )
