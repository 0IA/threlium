"""Якорь треда по ``X-Threlium-Space-Id``: один notmuch-запрос вместо tail-index.

По известному ``ThreliumSpaceB62Wire`` строит запрос
``tag:route AND from:<bridge> AND Threliumspace:"<wire>"`` → первое (newest)
сообщение = якорь; из якоря → thread scope → newest MID в треде.
"""
from __future__ import annotations

import notmuch2  # pyright: ignore[reportMissingImports]

import threlium.nm as nm
from threlium.types import (
    NotmuchBridgeFromLocalhost,
    NotmuchMessageIdInner,
    NotmuchQueryConnective,
    NotmuchTag,
    NotmuchThreadScopeId,
    ThreliumSpaceB62Wire,
)

_SORT_NEWEST = notmuch2.Database.SORT.NEWEST_FIRST


def _newest_message_mid_in_thread(
    db: notmuch2.Database, tid: NotmuchThreadScopeId,
) -> NotmuchMessageIdInner:
    """Message-ID самого нового сообщения в треде (любой тег / From)."""
    q = tid.as_notmuch_thread_term()
    for nm_msg in db.messages(q, sort=_SORT_NEWEST):
        return nm.require_inner_message_id_from_notmuch_message(nm_msg)
    raise RuntimeError(
        f"пустой тред при ненулевом якоре (thread={tid.value!r})"
    )


def resolve_bridge_tail_mid_for_space(
    db: notmuch2.Database,
    *,
    bridge: NotmuchBridgeFromLocalhost,
    space_wire: ThreliumSpaceB62Wire,
) -> NotmuchMessageIdInner | None:
    """Newest MID в треде, привязанном к ``space_wire``, или ``None`` для нового пространства.

    Алгоритм:
    1. Один запрос ``tag:route AND from:<bridge> AND Threliumspace:"<wire>"``
       с ``NEWEST_FIRST``, берём первое сообщение — якорь.
    2. Из якоря — ``thread:`` id → scope треда.
    3. По scope — newest message (любой тег) → его MID.

    ``None`` возвращается только если запрос пуст (первое сообщение в пространстве).
    """
    q = NotmuchQueryConnective.join_and(
        NotmuchTag.ROUTE.as_tag_query_term(),
        bridge.as_from_query_term(),
        space_wire.as_notmuch_index_term(),
    )
    anchor: notmuch2.Message | None = None
    for nm_msg in db.messages(q, sort=_SORT_NEWEST):
        anchor = nm_msg
        break
    if anchor is None:
        return None

    anchor_mid = nm.require_inner_message_id_from_notmuch_message(anchor)
    tid = nm.thread_id_for_header_message_id_in_db(db, anchor_mid)
    if tid is None:
        raise RuntimeError(
            f"якорь найден, но thread id отсутствует (mid={anchor_mid.value!r})"
        )
    return _newest_message_mid_in_thread(db, tid)
