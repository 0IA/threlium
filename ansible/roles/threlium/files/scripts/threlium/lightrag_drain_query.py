"""Сборка notmuch search для LightRAG-drain (pending-выборка) через доменные VO.

Единственный источник строки выборки «что ещё не проиндексировано и достойно
графа» — используется и в [`runners/lightrag/_drain.py`](runners/lightrag/_drain.py),
и в e2e-helpers (idle-wait / count). Никакой конкатенации сырых ``"tag:..."`` /
``"to:..."`` на стороне потребителя: всё через
:class:`~threlium.types.notmuch_query.NotmuchQueryConnective` /
``NotmuchQueryField`` / ``NotmuchQuery`` и :class:`~threlium.types.notmuch_tag.NotmuchTag`.

После унификации «достойно графа» = письмо несёт ``<history>``-часть
(:func:`threlium.mime_reform.message_has_history`), а не его ``To:``-стадия. notmuch не
индексирует MIME-части по Content-ID, поэтому selector даёт лишь tag-негативы (дешёвый
pre-filter), а финальный предикат ``message_has_history`` применяет load-time
``runners/lightrag/_drain.py`` (письма без history → ``lightrag_skipped``, не вечный pending).
"""
from __future__ import annotations

from threlium.types import (
    NotmuchQueryConnective,
    NotmuchTag,
)


def lightrag_drain_pending_search() -> str:
    """Notmuch search для pending LightRAG-drain (db.messages / count / e2e wait).

    ``* AND NOT unread AND NOT lightrag_indexed AND NOT lightrag_skipped
    AND NOT context_summarized``. Содержательность (``<history>``) — load-time предикат
    в ``_drain.py``, т.к. notmuch не умеет искать по Content-ID частей.
    """
    base_terms = [
        "*",
        NotmuchQueryConnective.negate(NotmuchTag.UNREAD.as_tag_query_term()),
        NotmuchQueryConnective.negate(NotmuchTag.LIGHTRAG_INDEXED.as_tag_query_term()),
        NotmuchQueryConnective.negate(NotmuchTag.LIGHTRAG_SKIPPED.as_tag_query_term()),
        NotmuchQueryConnective.negate(NotmuchTag.CONTEXT_SUMMARIZED.as_tag_query_term()),
    ]
    return NotmuchQueryConnective.join_and(*base_terms)
