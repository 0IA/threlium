"""Сборка notmuch search для LightRAG-drain (pending-выборка) через доменные VO.

Единственный источник строки выборки «что ещё не проиндексировано и достойно
графа» — используется и в [`runners/lightrag/_drain.py`](runners/lightrag/_drain.py),
и в e2e-helpers (idle-wait / count). Никакой конкатенации сырых ``"tag:..."`` /
``"to:..."`` на стороне потребителя: всё через
:class:`~threlium.types.notmuch_query.NotmuchQueryConnective` /
``NotmuchQueryField`` / ``NotmuchQuery`` и :class:`~threlium.types.notmuch_tag.NotmuchTag`.

Whitelist стадий — :func:`threlium.context_budget.content_indexable_stages`
(общий с enrich базис ``CONTEXT_ROLE_BY_TO_STAGE`` + memory-ящики). Positive
``to:(a OR b OR …)`` короче и устойчивее списка ``NOT to:…``: новая FSM-стадия
не попадёт в граф, пока её явно не включат в политику.
"""
from __future__ import annotations

from threlium.context_budget import content_indexable_stages
from threlium.types import (
    NotmuchQuery,
    NotmuchQueryConnective,
    NotmuchQueryField,
    NotmuchTag,
)


def lightrag_drain_pending_search() -> str:
    """Notmuch search для pending LightRAG-drain (db.messages / count / e2e wait).

    ``* AND NOT unread AND NOT lightrag_indexed AND NOT lightrag_skipped
    AND NOT context_summarized AND (to:<indexable> OR …)``.
    """
    base_terms = [
        "*",
        NotmuchQueryConnective.negate(NotmuchTag.UNREAD.as_tag_query_term()),
        NotmuchQueryConnective.negate(NotmuchTag.LIGHTRAG_INDEXED.as_tag_query_term()),
        NotmuchQueryConnective.negate(NotmuchTag.LIGHTRAG_SKIPPED.as_tag_query_term()),
        NotmuchQueryConnective.negate(NotmuchTag.CONTEXT_SUMMARIZED.as_tag_query_term()),
    ]
    to_terms = [
        NotmuchQueryField.TO.term(stage.rfc822_mailbox)
        for stage in sorted(content_indexable_stages(), key=lambda s: s.value)
    ]
    whitelist = NotmuchQuery.group(NotmuchQueryConnective.join_or(*to_terms))
    return NotmuchQueryConnective.join_and(*base_terms, whitelist)
