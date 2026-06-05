"""Резолв целевого треда голосованием по assistant-glue-MID (lookup-вариант).

Мост сверяет последние ``K`` assistant-ответов присланной истории с notmuch: их glue-MID
(``canon(IsomorphContentId(hash(R_i)))``) уже сминтил egress на прошлых ходах, значит они есть в
индексе. Поиск каждого → тред; голосование → целевой тред; ``In-Reply-To`` = самый свежий из
кандидатов, попавший в победивший тред (``G_j``). Устойчиво к коллизии последнего ответа: одиночный
неверный хеш перебивается консенсусом.

Контракт (MVP):

- тред найден, прошлое **сведено** → ``In-Reply-To = G_j`` (свежайший кандидат в треде-победителе);
- тред **не найден** (0 совпадений) → ``None`` → новый тред (orphan): сообщения такого нет у нас;
- тред найден, но **прошлый ход ещё в работе** (ingress'ов больше, чем glue-ответов — клиент прислал
  следующее, не дождавшись ответа) → :class:`IsomorphThreadInWork` (мост → 409, клиент ретраит);
- ничья верхушки голосов → :class:`AmbiguousIsomorphThread` (мост → 409);
- ошибка notmuch (БД недоступна) — пробрасывается наружу → мост возвращает ошибку.

Резолв вызывается **только** при наличии last-assistant; первый ход (ассистента нет) — orphan, решается
в :mod:`.history` без обращения сюда. notmuch читается синхронно (notmuch2 + контекст
:func:`threlium.nm.notmuch_database`) — вызывать из async-моста только через ``anyio.to_thread``.
"""
from __future__ import annotations

from threlium import nm
from threlium.types import (
    FsmStage,
    NotmuchBridgeFromLocalhost,
    NotmuchMessageIdInner,
    NotmuchQueryConnective,
    NotmuchQueryField,
    NotmuchThreadScopeId,
    RfcMessageIdWire,
)


class AmbiguousIsomorphThread(Exception):
    """Несколько тредов с одинаковым максимумом голосов — однозначно стыковать нельзя."""


class IsomorphThreadInWork(Exception):
    """Тред найден, но в нём есть несведённое сообщение (`tag:unread`) — прошлый ход ещё в FSM, а
    клиент прислал следующий. MVP: мост отвечает ошибкой (нельзя класть параллельный ход в тот же тред)."""


def _count(db: object, query: str) -> int:
    return sum(1 for _ in db.messages(query))  # type: ignore[attr-defined, misc]


def _thread_has_open_turn(db: object, thread_id: str) -> bool:
    """В треде isomorph-ingress'ов больше, чем ответов (egress_isomorph glue) → есть начатый ход без
    ответа: клиент прислал следующий, не дождавшись.

    Робастнее ``tag:unread``: фоновые ПОСТ-ответные стадии (reflect/memory/lightrag) не создают новых
    isomorph-ingress/glue → не дают ложный in-work на штатном продолжении. Каждый завершённый ход =
    1 ingress (``from:isomorph``) + 1 glue (``from:egress_isomorph``); незакрытый → ingress без glue.
    """
    thread = NotmuchQueryField.THREAD.term(thread_id)
    n_ingress = _count(db, NotmuchQueryConnective.join_and(
        thread, NotmuchBridgeFromLocalhost.ISOMORPH.as_from_query_term()))
    n_glue = _count(db, NotmuchQueryConnective.join_and(
        thread, NotmuchQueryField.FROM.term(FsmStage.EGRESS_ISOMORPH.rfc822_mailbox)))
    return n_ingress > n_glue


def resolve_in_reply_to(
    candidate_mids: tuple[RfcMessageIdWire, ...], *, max_replies: int
) -> RfcMessageIdWire | None:
    """Голосование по glue-MID последних assistant-ответов (most-recent-first) → IRT.

    ``candidate_mids`` — ``canon(hash(R_i))`` ответов, самый свежий первым (непустой). Возвращает
    ``G_j`` (свежайший кандидат в треде-победителе) либо ``None`` (тред не найден → новый тред).
    Бросает :class:`IsomorphThreadInWork` (тред найден, но прошлый ход не сведён) или
    :class:`AmbiguousIsomorphThread` (ничья). Ошибки notmuch пробрасываются.
    """
    cands = candidate_mids[: max(1, max_replies)]
    if not cands:
        return None

    with nm.notmuch_database(write=False) as db:
        thread_of: list[str | None] = []
        votes: dict[str, int] = {}
        for mid in cands:
            inner = NotmuchMessageIdInner.from_optional_wire(mid)
            tid: str | None = None
            if inner is not None:
                msg = nm.first_notmuch_message_for_inner_id(db, inner)
                if msg is not None:
                    scope = NotmuchThreadScopeId.from_notmuch_thread_attr(msg.threadid)
                    tid = scope.value if scope is not None else None
            thread_of.append(tid)
            if tid is not None:
                votes[tid] = votes.get(tid, 0) + 1

        if not votes:
            return None  # ни один glue не найден → нового сообщения тред у нас нет → orphan

        top = max(votes.values())
        winners = [t for t, c in votes.items() if c == top]
        if len(winners) > 1:
            raise AmbiguousIsomorphThread(
                f"isomorph: ambiguous thread vote (top={top}, threads={sorted(winners)})"
            )
        winner = winners[0]
        if _thread_has_open_turn(db, winner):
            raise IsomorphThreadInWork(
                f"isomorph: prior turn still in flight in thread {winner} (sent ahead of reply)"
            )

    for mid, tid in zip(cands, thread_of, strict=True):
        if tid == winner:
            return mid  # most-recent-first → первый матч = самый свежий ответ в треде
    return None
