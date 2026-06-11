"""HITL-ветвление родителя для ingress: обход предков по IRT до cli_hitl_out (§2 плана).

Единственная фабрика union ``HitlParentRouting``.
Ответ пользователя ссылается на egress_* (не на cli_hitl_out), поэтому нужен
короткий подъём по In-Reply-To (типично 1–3 шага), пока не найдён
``From: cli_hitl_out@localhost`` или не встречен reasoning/ingress (не-HITL).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import msgspec
import notmuch2  # pyright: ignore[reportMissingImports]

from .fsm_stage import FsmStage

if TYPE_CHECKING:
    from .notmuch_snapshot import IrtAncestorSnapshot

_MAX_HITL_WALK_DEPTH = 5

_NON_HITL_STAGES = frozenset({
    FsmStage.REASONING,
    FsmStage.INGRESS,
    FsmStage.ENRICH,
    FsmStage.SUBAGENT_INTENT,
    FsmStage.SUBAGENT_END,
})


class HitlParentWithoutIntent(msgspec.Struct, frozen=True):
    """Родитель без HITL-маркера → enrich."""


class HitlParentWithIntent(msgspec.Struct, frozen=True):
    """HITL-маркер: обход IRT-предков нашёл cli_hitl_out → маршрутизация в cli_resume."""


HitlParentRouting = HitlParentWithoutIntent | HitlParentWithIntent


def classify_hitl_parent_notmuch(
    db: notmuch2.Database,
    parent_snap: "IrtAncestorSnapshot",
) -> HitlParentRouting:
    """Детекция HITL: обход предков по IRT от parent-СНИМКА (1-N шагов) до cli_hitl_out.

    Вызывается из ``ingress.main`` под уже открытым READ ``db`` (тот же сеанс, что и lookup родителя;
    обёртка ``nm.read_retry`` на стороне ingress переоткроет при discard'е ревизии). Работает на
    иммутабельных ``IrtAncestorSnapshot`` (живой ``notmuch2.Message`` не передаётся в эту бизнес-логику);
    ``db`` нужен лишь для подъёма по IRT (``db.find`` — граница), где каждый предок СРАЗУ снимается в
    снимок. Наружу — плоский ``HitlParentRouting`` VO.

    Алгоритм:
        1. Начать с parent-снимка.
        2. Если From: cli_hitl_out → HITL → cli_resume.
        3. Если From: reasoning/ingress/enrich/subagent_* → не HITL.
        4. Иначе (egress_router, egress_*) → подняться по IRT (In-Reply-To) на шаг вверх.
        5. Лимит MAX_HITL_WALK_DEPTH шагов.
    """
    from threlium import nm as _nm
    from .notmuch_snapshot import snapshot_from_nm_message

    current = parent_snap
    for _ in range(_MAX_HITL_WALK_DEPTH):
        if current.is_sent_from_fsm_stage(FsmStage.CLI_HITL_OUT):
            return HitlParentWithIntent()

        for stage in _NON_HITL_STAGES:
            if current.is_sent_from_fsm_stage(stage):
                return HitlParentWithoutIntent()

        parent_inner = current.in_reply_to_inner()
        if parent_inner is None:
            return HitlParentWithoutIntent()

        next_msg = _nm.first_notmuch_message_for_inner_id(db, parent_inner)
        if next_msg is None:
            return HitlParentWithoutIntent()
        current = snapshot_from_nm_message(next_msg, parent_inner)  # граница → снимок

    return HitlParentWithoutIntent()
