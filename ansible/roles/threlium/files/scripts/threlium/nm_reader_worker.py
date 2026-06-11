"""Изолированный notmuch multi-result reader — выполняется в forkserver-ВОРКЕРЕ, не в движке.

ПРИЧИНА (заземлено на исходники notmuch2 0.38.3, ``/usr/lib/python3/dist-packages/notmuch2/``):

Ленивый итератор ``db.messages(query)`` — единственный multi-result доступ в биндинге
(``_database.py`` ``Database.messages`` → ``MessageIter``; ``_query.py`` ``Query.messages``). Его обход
(``_base.py`` ``NotmuchIter.__next__`` ← наследует ``_message.py`` ``MessageIter.__next__``) дёргает три
C-функции БЕЗ error-канала (cdef ``_build.py`` ~стр. 218-226):
    * ``notmuch_messages_valid``      → ``notmuch_bool_t`` (нет статуса);
    * ``notmuch_messages_get``        → ``notmuch_message_t *`` (нет статуса);
    * ``notmuch_messages_move_to_next`` → **``void``** (нет статуса);   ← ключевое
    * ``notmuch_messages_destroy``    → ``void`` (деструктор тоже).
Когда конкурентный writer коммитит, Xapian-снимок читателя устаревает, и следующая из этих операций
бросает C++ ``Xapian::DatabaseModifiedError``. Через void/bool/ptr CFFI-границу C++-исключение НЕ
конвертируется в Python-ошибку → ``std::terminate`` → **SIGABRT ВСЕГО процесса** (``read_retry``
бессилен — ловить нечего).

КОНТРАСТ — почему остальной доступ безопасен (status-checked → Python-исключение → ``read_retry``):
    * ``_database.py`` ``Database.__init__`` (open), ``find``, ``count_messages`` — проверяют ``ret`` →
      ``NotmuchError`` (``_errors.py``);
    * ``_message.py`` ``Message.header`` → NULL → ``NullPointerError``; ``Message.path`` →
      ``ffi.string(NULL)`` → голый ``RuntimeError`` («cannot use string() on …»).
Поэтому корень — ИМЕННО итератор; ``db.find(id)`` (одиночный lookup) безопасен, на нём строится родитель.

РЕШЕНИЕ: обход итератора — в forkserver-воркере. SIGABRT убивает ВОРКЕР, движок выживает (родитель ловит
``BrokenProcessPool`` и ретраит на свежем снимке). ``forkserver`` (не ``fork``): воркеры форкаются из
чистого single-threaded server'а, а не из многопоточного движка — иначе fork-в-многопоточном наследует
залоченные чужими тредами мьютексы → deadlock (``__init__.py`` / ``_base.py NotmuchObject`` описывают
иерархическую модель памяти libnotmuch и опасность ``__del__`` при ещё живых ссылках).

Контракт: модуль МИНИМАЛЕН (только ``notmuch2``) — он в ``set_forkserver_preload``, не должен тянуть
пакет ``threlium``. Наружу — **только ``list[str]``** (message-id / path); живой ``notmuch2.Message`` не
покидает воркер (объекты невалидны вне своего ``db``; сериализация строк дешёвая). VO-граница
(``NotmuchMessageIdInner`` / ``Path``) — на стороне родителя (``nm.notmuch_query_message_ids``).
"""
from __future__ import annotations

import notmuch2  # pyright: ignore[reportMissingImports]

# Строковые имена сортировок (picklable; родитель не шлёт notmuch2-энумы в воркер).
_SORT = {
    "newest": notmuch2.Database.SORT.NEWEST_FIRST,
    "oldest": notmuch2.Database.SORT.OLDEST_FIRST,
    "unsorted": notmuch2.Database.SORT.UNSORTED,
}

# Поле, извлекаемое из каждого сообщения (минимальное — чтобы окно итератора было крошечным).
FIELD_MESSAGEID = "messageid"
FIELD_PATH = "path"


def fetch_message_field(
    db_path: str,
    query: str,
    *,
    sort: str = "unsorted",
    limit: int | None = None,
    field: str = FIELD_MESSAGEID,
) -> list[str]:
    """Eager-fetch одного поля (``messageid`` | ``path``) по ``query`` в ВОРКЕРЕ → ``list[str]``.

    Жадно (eager) и плотно: читаем только запрошенное поле в тугом цикле, чтобы окно ленивого
    Xapian-итератора (между ``move_to_next``) было минимальным. Возможные исходы в воркере:
    - SIGABRT (C++ ``DatabaseModifiedError`` из ``move_to_next``) → процесс умирает → родитель видит
      ``BrokenProcessPool`` → ретрай;
    - ``RuntimeError`` cffi-NULL / ``notmuch2.NullPointerError`` / ``XapianError`` (поле на
      инвалидированном message) → пробрасывается → ``future.result()`` поднимает у родителя → ретрай.
    В обоих случаях движок не падает."""
    sort_enum = _SORT[sort]
    out: list[str] = []
    with notmuch2.Database(db_path, mode=notmuch2.Database.MODE.READ_ONLY) as db:
        for msg in db.messages(query, sort=sort_enum):
            if field == FIELD_PATH:
                out.append(str(msg.path))
            else:
                out.append(str(msg.messageid))
            if limit is not None and len(out) >= limit:
                break
    return out
