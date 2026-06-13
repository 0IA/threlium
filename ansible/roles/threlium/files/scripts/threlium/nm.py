"""Обёртка над дистрибутивным notmuch2 (libnotmuch): без CLI ``notmuch``.

Открытие union-индекса — только через :func:`notmuch_database` (короткий ``with`` на один
связный набор операций: запрос, батч моста, проход IRT). Не держите БД открытой на всю
стадию FSM или долгий HTTP — это порождает лишнюю конкуренцию и удлиняет жизнь
``notmuch2.Message`` сверх контракта libnotmuch.

**Контракт:** функции с аргументом ``db: notmuch2.Database`` вызываются только под
открытым ``with notmuch_database``. Публичные функции этого модуля **не возвращают**
``notmuch2.Message`` наружу (после ``with`` объект недействителен). Для обхода с
доступом к полям — генератор внутри одного ``with`` (например IRT-цепочка) или
извлечение примитивов/VO внутри сеанса у вызывающего.

Единый READ-примитив — декоратор :data:`read_retry` (tenacity, reopen-on-modified): self-opening
функция сама открывает короткий ``with notmuch_database(write=False)``, БЫСТРО материализует всё в VO и
возвращает их; при discard'е ревизии под конкурентной записью сеанс переоткрывается. Родитель по
``In-Reply-To`` материализуется в ``ingress`` (``parent_message_for_in_reply_in_db`` под ``read_retry``).
Предок маршрута для egress — через IRT-цепочку
(:func:`~threlium.ingress_route_resolve.resolve_egress_task_route_ancestor`):
``ResolvedRoute`` содержит материализованный снимок (``EgressAncestorSnapshot``),
повторное открытие БД для чтения предка не требуется.

`docs/INDEX.md` §5.5.3, §9.1: здесь живёт ``nm_settle(inner)`` —
единственная атомарная операция «снять unread + перевести new/→cur/<id>:2,S»
для stage worker'а; и ``settle_recovery_for_stage(stage)`` — startup-recovery
«после rename(2), до Xapian-commit». Функции индексации отдельного archive-Maildir
(``run_archive_index``/``index_maildir_under_database_path``/``_remove_stale_under_prefix``)
удалены: единственный writer'ы Xapian'а — fdm (через CLI ``notmuch insert``)
и RAG-loop в ``threlium-engine`` (через тег :attr:`~threlium.types.NotmuchTag.LIGHTRAG_INDEXED` под db.atomic).
"""
from __future__ import annotations

import concurrent.futures
import configparser
import functools
import logging
import multiprocessing
import os
import threading
import time
from concurrent.futures.process import BrokenProcessPool
from email.message import EmailMessage
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

import notmuch2  # pyright: ignore[reportMissingImports]
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from threlium.logutil import logger

log = logger.bind(component=__name__)
_RETRY_STDLOG = logging.getLogger(__name__)

_RETRY_MAX_ATTEMPTS = 5
_RETRY_WAIT = wait_exponential(multiplier=0.1, min=0.1, max=2)
_RETRY_STOP = stop_after_attempt(_RETRY_MAX_ATTEMPTS)


def _is_retryable_xapian(exc: BaseException) -> bool:
    if not isinstance(exc, notmuch2.XapianError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "modified" in msg


_RETRY_CONDITION = retry_if_exception(_is_retryable_xapian)
_RETRY_BEFORE_SLEEP = before_sleep_log(_RETRY_STDLOG, logging.WARNING)


# CFFI-сигнатура ``ffi.string(NULL)``: notmuch2-геттеры (``msg.path`` и др.) на инвалидированном
# discard'ом сообщении возвращают NULL → cffi бросает ГОЛЫЙ ``RuntimeError`` с этим текстом, НЕ
# ``notmuch2.NullPointerError`` (см. ``is_concurrent_revision_discard``).
_CFFI_NULL_STRING_SIGNATURE = "cannot use string() on"


def is_concurrent_revision_discard(exc: BaseException) -> bool:
    """READ под конкурентной записью: writer закоммитил, Xapian **отбросил ревизию** открытого
    read-снапшота → in-flight чтение/материализация падает. ТРИ формы (все — один и тот же discard,
    лечится переоткрытием БД и повтором, см. :data:`read_retry`):

    1. ``notmuch2.XapianError`` («A Xapian exception occurred»);
    2. ``notmuch2.NullPointerError`` (биндинг вернул NULL на инвалидированном message/db);
    3. голый ``RuntimeError`` от CFFI ``ffi.string(NULL)`` — ``notmuch2._message.path`` (и др. string-
       геттеры) на discard'нутом message читают NULL-указатель; notmuch2 **не** оборачивает это в свой
       тип. Без распознавания он пролетает мимо ``read_retry`` → ``_collect_batch`` / route-resolve /
       IRT-материализация падает, осиротевший ``notmuch2.Message`` в GC зовёт C++ destructor на устаревшей
       ревизии → ``Xapian::DatabaseModifiedError`` → ``std::terminate`` → **SIGABRT движка** (рвёт
       in-flight lancedb-запись → каскад). Это было источником -n12 крэш-каскадов.

    Подтверждено профилированием: рвётся не линейность IRT-цепочки (``docs/THREAD_MODEL.md`` §3), а
    read-снапшот; notmuch single-writer/many-readers корректен только при reopen-on-modified у читателя."""
    if isinstance(exc, (notmuch2.XapianError, notmuch2.NullPointerError)):
        return True
    return isinstance(exc, RuntimeError) and _CFFI_NULL_STRING_SIGNATURE in str(exc)


# Обратная совместимость имени (приватный алиас на публичный предикат).
_is_concurrent_revision_discard = is_concurrent_revision_discard


_RETRY_READ_CONDITION = retry_if_exception(is_concurrent_revision_discard)

from threlium.types import (
    MailHeaderName,
    NotmuchMessageIdInner,
    NotmuchQueryConnective,
    NotmuchQueryField,
    NotmuchTag,
    NotmuchThreadScopeId,
    RfcInReplyToWire,
    RfcMessageIdWire,
)


def require_inner_message_id_from_notmuch_message(msg: notmuch2.Message) -> NotmuchMessageIdInner:
    """Inner ``Message-ID`` из индекса libnotmuch; без парсящегося id — ``RuntimeError``.

    Инвариант пайплайна Threlium: любое ``notmuch2.Message`` из ``db.messages`` / ``db.get``
    обязано иметь непустой и нормализуемый id (без молчаливых ``continue`` у вызывающего).
    """
    fp = Path(msg.path)
    try:
        raw = str(msg.messageid)
    except Exception as e:
        raise RuntimeError(f"notmuch Message без читаемого Message-ID (path={fp})") from e
    inner = NotmuchMessageIdInner.from_optional_raw(raw)
    if inner is None:
        raise RuntimeError(
            f"notmuch Message-ID пустой или не парсится (path={fp}, raw={raw!r})"
        )
    return inner


def require_inner_message_id_from_fsm_email(msg: EmailMessage) -> NotmuchMessageIdInner:
    """Inner ``Message-ID`` с конверта FSM-задачи; без парсящегося id — ``RuntimeError``."""
    mid_w = RfcMessageIdWire.parse_present_from_email(msg, MailHeaderName.MESSAGE_ID)
    inner = NotmuchMessageIdInner.from_optional_wire(mid_w)
    if inner is None:
        raise RuntimeError(
            "FSM-инвариант: задача без парсящегося inner Message-ID "
            f"({MailHeaderName.MESSAGE_ID.value})"
        )
    return inner


def require_fsm_message_id(
    msg: EmailMessage, stage_name: str
) -> tuple[RfcMessageIdWire, NotmuchMessageIdInner]:
    """``(wire, inner)`` ``Message-ID`` с конверта FSM-задачи; иначе ``RuntimeError``.

    Стадии, которым нужен и present-wire (для логов), и inner (для CRDT/settle):
    ``mid_w, inner = require_fsm_message_id(msg, "<stage>")``. При отсутствии
    парсящегося id — ``RuntimeError`` с именем стадии.
    """
    mid_w = RfcMessageIdWire.parse_present_from_email(msg, MailHeaderName.MESSAGE_ID.value)
    inner = NotmuchMessageIdInner.from_optional_wire(mid_w)
    if mid_w is None or inner is None:
        raise RuntimeError(f"{stage_name}: no Message-ID on incoming message")
    return mid_w, inner


def _require_message_id_with_db(db: notmuch2.Database, file_path: Path) -> NotmuchMessageIdInner:
    msg = db.get(str(file_path.resolve()))
    return require_inner_message_id_from_notmuch_message(msg)



def header_field_optional(msg: notmuch2.Message, name: MailHeaderName) -> str | None:
    """Значение заголовка; ``name`` — только :class:`~threlium.mail_header_names.MailHeaderName`.

    ``LookupError`` — заголовок пуст/нет → ``None``. ``notmuch2.NullPointerError`` **НЕ глотаем**:
    это сигнал discard'нутой ревизии под конкурентной записью (а не «нет заголовка») — даём ему
    всплыть в :data:`read_retry`, который переоткроет БД и материализует заново. Глотание
    его как «отсутствие» молча портило бы данные (см. :func:`_is_concurrent_revision_discard`)."""
    try:
        return str(msg.header(name.value))
    except LookupError:
        # Отсутствие заголовка — штатный present-or-None (НЕ ошибка); без exc_info (трейсбек = GIL-хотспот).
        log.debug("nm_header_absent", header=name.value)
        return None


def _thread_id_for_resolved_path_in_db(db: notmuch2.Database, abs_path: Path) -> NotmuchThreadScopeId | None:
    msg = db.get(str(abs_path.resolve()))
    return NotmuchThreadScopeId.from_notmuch_thread_attr(msg.threadid)


@functools.cache
def database_dir_from_config() -> Path:
    # Путь к union-notmuch БД статичен на жизнь процесса (NOTMUCH_CONFIG / ~/.notmuch-config не меняются).
    # Кэшируем РОВНО раз: раньше каждый notmuch_database()/обход IRT/батч drain заново stat'ил + парсил
    # INI через configparser — измеримая лишняя работа на rag-loop под -n4 (py-spy: configparser.read в
    # стеках drain и egress_router IRT-обхода). Движок перезапускается на cold-reset → кэш свежий.
    cfg_path = Path(os.environ.get("NOTMUCH_CONFIG", Path.home() / ".notmuch-config"))
    if not cfg_path.is_file():
        raise FileNotFoundError(f"notmuch config missing: {cfg_path}")
    cp = configparser.ConfigParser()
    cp.read(cfg_path)
    raw = cp.get("database", "path", fallback="").strip()  # keyword ``fallback`` у ConfigParser.get
    if not raw:
        raise ValueError("notmuch config [database] path is empty")
    return Path(raw).expanduser().resolve()


@contextmanager
def notmuch_database(*, write: bool = False) -> Generator[notmuch2.Database, None, None]:
    """Контекст union-notmuch на один связный набор операций (READ или WRITE).

    При ``XapianError`` с ``locked`` / ``modified`` — retry с экспоненциальной
    задержкой (tenacity, до ``_RETRY_MAX_ATTEMPTS`` попыток). Не открывайте на всю
    стадию FSM или на время долгих внешних вызовов; держите область такой же короткой,
    как сейчас у мостов (один батч updates + хвост ``tag:route``).
    """
    path = database_dir_from_config()
    mode = notmuch2.Database.MODE.READ_WRITE if write else notmuch2.Database.MODE.READ_ONLY

    @retry(retry=_RETRY_CONDITION, stop=_RETRY_STOP, wait=_RETRY_WAIT, before_sleep=_RETRY_BEFORE_SLEEP, reraise=True)
    def _open() -> notmuch2.Database:
        return notmuch2.Database(str(path), mode=mode)

    db = _open()
    try:
        yield db
    finally:
        # close() освобождает Xapian-локи, но НЕ обнуляет указатель → db.alive остаётся True.
        # Поэтому осиротевшие дочерние notmuch2-объекты (Message/Messages, утёкшие в traceback пойманной
        # XapianError и пережившие сессию до цикличного GC) при ``__del__`` видят родителя живым → зовут
        # CFFI ``notmuch_*_destroy`` на устаревшей ревизии → C++ ``Xapian::DatabaseModifiedError`` из
        # деструктора (notmuch2 ловит лишь Python ``ObjectDestroyedError``) → ``std::terminate`` → SIGABRT,
        # МИМО read_retry (краш в GC, вне try). ``_destroy()`` после ``close()`` делает полный
        # ``notmuch_database_destroy`` и ОБНУЛЯЕТ ``_db_p`` → ``db.alive``=False → у любого осиротевшего
        # ребёнка ``_destroy`` становится no-op (notmuch2: «ensure it's not been destroyed by its parent»),
        # без обращения к Xapian. Детерминированно, без чистки traceback. ``close()`` уже закрыл БД, поэтому
        # ``_destroy`` лишь освобождает talloc, не трогая Xapian.
        try:
            db.close()
        finally:
            db._destroy()


#: Декоратор **reopen-on-modified** для self-opening READ-функций (tenacity — как существующий
#: ``notmuch_database._open`` / ``nm_settle``, без своих велосипедов). Контракт декорируемой функции
#: (DDD VO, ``docs/TYPES.md`` «границы API»): сама открывает короткий ``with notmuch_database(write=False)``,
#: БЫСТРО материализует всё нужное в иммутабельные VO (snapshot / ``Path`` / доменный VO) и возвращает
#: **их** — НИКОГДА живой ``notmuch2.Message`` (валиден лишь пока открыта его ``db``). При discard'е
#: ревизии под конкурентной записью (:func:`_is_concurrent_revision_discard`) tenacity повторяет вызов —
#: функция переоткрывает свежую ``db`` и материализует с нуля (идемпотентно, наружу только VO). Bounded
#: (``_RETRY_MAX_ATTEMPTS``), exp backoff, reraise. (cachetools тут не подходит — это кэш, а нам нужен
#: СВЕЖИЙ снимок, а не переиспользование инвалидированного.)
read_retry = retry(
    retry=_RETRY_READ_CONDITION,
    stop=_RETRY_STOP,
    wait=_RETRY_WAIT,
    before_sleep=_RETRY_BEFORE_SLEEP,
    reraise=True,
)


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# Изолированный multi-result reader (forkserver-воркер) — ЕДИНОЕ API для всех ``db.messages(query)``.
#
# ПРИЧИНА (исходники notmuch2 0.38.3, ``/usr/lib/python3/dist-packages/notmuch2/``; полная цитата — в
# ``threlium/nm_reader_worker.py``): обход ленивого итератора ``db.messages`` (``_base.py``
# ``NotmuchIter.__next__``) дёргает ``notmuch_messages_{valid,get,move_to_next,destroy}`` — bool/ptr/
# **void**/void БЕЗ error-канала (cdef ``_build.py`` ~218-226). При устаревании Xapian-снимка под
# конкурентным commit'ом эти функции бросают C++ ``Xapian::DatabaseModifiedError``, который через
# void-CFFI-границу НЕ становится Python-ошибкой → ``std::terminate`` → SIGABRT ВСЕГО движка (поймать
# нельзя; ``read_retry`` бессилен). Контраст: ``open``/``find``/``count`` (``_database.py``) и
# ``Message.header``/``path`` (``_message.py``) — status/NULL-checked → Python-исключение → ``read_retry``;
# поэтому ``db.find(id)`` безопасен, на нём строится родитель.
#
# РЕШЕНИЕ: обход — в forkserver-ВОРКЕРЕ. SIGABRT убивает воркер, движок жив; родитель ловит
# ``BrokenProcessPool`` и ретраит на свежем снимке. Воркер → ``list[str]`` (message-id/path), VO-граница
# у родителя; per-message данные — ``db.find(id)`` (без итератора). ``forkserver`` (НЕ ``fork``): воркеры
# форкаются из ЧИСТОГО single-threaded server'а, поднятого рано в engine main, а НЕ из многопоточного
# движка → нет fork-в-многопоточном (залоченные чужими тредами мьютексы) deadlock'а (модель памяти
# libnotmuch — ``__init__.py`` / ``_base.py NotmuchObject``). ``warm_nm_reader()`` ОБЯЗАН вызываться в
# engine main ДО старта rag-loop/handler-тредов.
# ─────────────────────────────────────────────────────────────────────────────────────────────────

_NM_READER_MAX_WORKERS = 4  # параллельные читатели (notmuch many-readers); >1 чтобы не сериализовать
_NM_READER_CTX: "multiprocessing.context.BaseContext | None" = None
_NM_READER_POOL: "concurrent.futures.ProcessPoolExecutor | None" = None
_NM_READER_LOCK = threading.Lock()


def _new_nm_reader_pool() -> "concurrent.futures.ProcessPoolExecutor":
    assert _NM_READER_CTX is not None
    return concurrent.futures.ProcessPoolExecutor(
        max_workers=_NM_READER_MAX_WORKERS, mp_context=_NM_READER_CTX
    )


def warm_nm_reader() -> None:
    """Поднять forkserver-server РАНО (engine main, до старта тредов; см. блок выше). Идемпотентно.

    Прогревочный submit форсирует старт server'а + первого воркера, пока процесс ОДНОПОТОЧНЫЙ —
    тогда server форкается из чистого main, а воркеры из server'а (safe). Без этого первый реальный
    запрос поднял бы server уже из многопоточного движка (fork-в-многопоточном hazard)."""
    global _NM_READER_CTX, _NM_READER_POOL
    with _NM_READER_LOCK:
        if _NM_READER_POOL is not None:
            return
        ctx = multiprocessing.get_context("forkserver")
        ctx.set_forkserver_preload(["notmuch2", "threlium.nm_reader_worker"])
        _NM_READER_CTX = ctx
        _NM_READER_POOL = _new_nm_reader_pool()
        pool = _NM_READER_POOL
    from threlium import nm_reader_worker  # noqa: PLC0415 — lazy: минимальный модуль (только notmuch2)
    try:
        db_path = str(database_dir_from_config())
        pool.submit(
            nm_reader_worker.fetch_message_field,
            db_path, "id:__nm_reader_warm_probe__", sort="unsorted", limit=1,
        ).result(timeout=30)
        log.info("nm_reader_warm_ok", max_workers=_NM_READER_MAX_WORKERS)
    except Exception as exc:  # прогрев best-effort; реальный запрос поднимет server лениво при нужде
        log.warning("nm_reader_warm_probe_failed", error=repr(exc))


def _recreate_nm_reader_pool(broken: "concurrent.futures.ProcessPoolExecutor") -> None:
    """Пересоздать пул ПОСЛЕ ``BrokenProcessPool`` (воркер умер от SIGABRT). Только первый поток,
    увидевший именно ЭТОТ сломанный пул, пересоздаёт (без thundering-herd). forkserver-server жив —
    новые воркеры форкаются из него (не из движка)."""
    global _NM_READER_POOL
    with _NM_READER_LOCK:
        if _NM_READER_POOL is broken or _NM_READER_POOL is None:
            _NM_READER_POOL = _new_nm_reader_pool()
            if broken is not None:
                broken.shutdown(wait=False)


def _run_nm_reader_field(query: str, sort: str, limit: int | None, field: str) -> list[str]:
    """Выполнить multi-result чтение в воркере с ретраем на ``BrokenProcessPool`` (SIGABRT воркера)
    и на concurrent-revision-discard (cffi-NULL / Xapian, пробро­шенный из воркера)."""
    from threlium import nm_reader_worker  # noqa: PLC0415
    if _NM_READER_POOL is None:
        warm_nm_reader()  # ленивый fallback (в норме warm в engine main); может форкнуть из тредов — best-effort
    db_path = str(database_dir_from_config())
    last_exc: BaseException | None = None
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        pool = _NM_READER_POOL
        if pool is None:
            raise RuntimeError("nm_reader pool unavailable")
        try:
            fut = pool.submit(
                nm_reader_worker.fetch_message_field,
                db_path, query, sort=sort, limit=limit, field=field,
            )
            return fut.result()
        except BrokenProcessPool as exc:
            last_exc = exc
            log.warning(
                "nm_reader_worker_died_retry",
                attempt=attempt + 1, query=query, field=field, err="BrokenProcessPool",
            )
            _recreate_nm_reader_pool(pool)
        except (notmuch2.XapianError, notmuch2.NullPointerError, RuntimeError) as exc:
            if not is_concurrent_revision_discard(exc):
                raise
            last_exc = exc
            log.warning(
                "nm_reader_discard_retry",
                attempt=attempt + 1, query=query, field=field, err=type(exc).__name__,
            )
        time.sleep(min(2.0, 0.1 * (2 ** attempt)))
    raise RuntimeError(
        f"nm_reader failed after {_RETRY_MAX_ATTEMPTS} attempts (query={query!r}, field={field})"
    ) from last_exc


def notmuch_query_message_ids(
    query: str, *, sort_newest_first: bool = False, sort_oldest_first: bool = False,
    limit: int | None = None,
) -> list[NotmuchMessageIdInner]:
    """**ЕДИНОЕ API multi-result чтения notmuch:** ``query`` → ``list[NotmuchMessageIdInner]``.

    Заменяет все ``for msg in db.messages(query)`` (краш-источник). Обход изолирован в forkserver-
    воркере (crash-safe), наружу — id-строки → VO здесь. Per-message данные (path/заголовки) —
    ``db.find(id)`` у вызывающего (status-checked). Порядок: newest/oldest/unsorted."""
    sort = "newest" if sort_newest_first else ("oldest" if sort_oldest_first else "unsorted")
    raw = _run_nm_reader_field(query, sort, limit, "messageid")
    out: list[NotmuchMessageIdInner] = []
    for s in raw:
        inner = NotmuchMessageIdInner.from_optional_raw(s)
        if inner is not None:
            out.append(inner)
    return out


def notmuch_query_message_paths(
    query: str, *, sort_newest_first: bool = False, limit: int | None = None,
) -> list[Path]:
    """Multi-result чтение → ``list[Path]`` (тот же изолированный воркер; field=path).

    Для мест, которым нужны только пути (без ``db.find``). Возвращает пути как есть из индекса."""
    sort = "newest" if sort_newest_first else "unsorted"
    raw = _run_nm_reader_field(query, sort, limit, "path")
    return [Path(s) for s in raw]


@read_retry
def inner_message_id_for_path(file_path: Path) -> NotmuchMessageIdInner:
    """Зафиксировать inner ``Message-ID`` по path в индексе (граница FSM после find).

    Вызывать сразу после ``_find_unread_in_thread``, до долгого handler'а — settle
    идёт по ``inner``, а не по path (path может устареть после rename/crash).
    """
    with notmuch_database(write=False) as db:
        return _require_message_id_with_db(db, file_path)


def _prepare_settle_target(db: notmuch2.Database, inner: NotmuchMessageIdInner) -> None:
    """Убедиться, что ``db.find(inner)`` сработает; при desync в ``cur/`` — recovery."""
    try:
        db.find(inner.value)
        return
    except LookupError as exc:
        log.warning("nm_settle_find_miss_recovering", inner=inner.value, exc_info=exc)
    msg = first_notmuch_message_for_inner_id(db, inner)
    if msg is None:
        raise RuntimeError(
            f"nm_settle: message not in notmuch index (inner={inner.value!r})"
        )
    fp = Path(msg.path)
    if fp.parent.name == "cur":
        log.info(
            "nm_settle_db_find_miss_recovery",
            inner=inner.value,
            path=str(fp),
        )
        with db.atomic():
            msg.tags.from_maildir_flags()
        return
    log.info(
        "nm_settle_db_find_miss_query",
        inner=inner.value,
        path=str(fp),
    )


@retry(retry=_RETRY_CONDITION, stop=_RETRY_STOP, wait=_RETRY_WAIT, before_sleep=_RETRY_BEFORE_SLEEP, reraise=True)
def nm_settle(inner: NotmuchMessageIdInner) -> None:
    """`docs/INDEX.md` §5.5.3: атомарный settle одного письма по inner Message-ID.

    Публичный API только ``NotmuchMessageIdInner`` (см. ``docs/TYPES.md``).
    При ``LookupError`` на ``db.find`` — поиск через ``inner.as_notmuch_term()``;
    для файла в ``cur/`` с рассинхроном тегов — ``from_maildir_flags()`` под recovery.

    Под `db.atomic()`:
      * ``msg.tags.discard`` с тегом ``NotmuchTag.UNREAD`` (wire ``unread``);
      * ``msg.tags.to_maildir_flags()`` — `rename(2)` ``new/<id>`` → ``cur/<id>:2,S``.

    Retry (tenacity) при retryable ``XapianError`` (lock contention).
    """
    with notmuch_database(write=True) as db:
        _prepare_settle_target(db, inner)
        with db.atomic():
            msg = db.find(inner.value)
            msg.tags.discard(NotmuchTag.UNREAD.value)
            msg.tags.to_maildir_flags()


@retry(retry=_RETRY_CONDITION, stop=_RETRY_STOP, wait=_RETRY_WAIT, before_sleep=_RETRY_BEFORE_SLEEP, reraise=True)
def settle_recovery_for_stage(stage: str) -> None:
    """`docs/INDEX.md` §9.1: startup-recovery ``from_maildir_flags()``.

    Лечит crash-окно «после rename(2), до Xapian-commit'а»: для всех писем
    с ``folder:<stage>/Maildir`` и тегом ``unread`` (см. ``NotmuchTag.UNREAD``), физически лежащих в ``cur/``,
    библиотека выравнивает теги по реальным Maildir-флагам файла (снимает
    ``unread`` потому что ``S`` присутствует) и обновляет path-индекс.

    Материализация: пути вычитываются списком, затем поштучно обрабатываются
    под ``db.atomic()`` (минимизация окна ленивого C-итератора Xapian).
    """
    q = NotmuchQueryConnective.join_and(
        NotmuchQueryField.FOLDER.term(f"{stage}/Maildir", quoted=True),
        NotmuchTag.UNREAD.as_tag_query_term(),
    )
    # Пути собираем изолированным reader'ом (не ленивый db.messages-итератор → C++ move_to_next
    # terminate), затем settle поштучно под WRITE db.atomic (db.get — single lookup, без итератора).
    cur_paths = [p for p in notmuch_query_message_paths(q) if p.parent.name == "cur"]
    if not cur_paths:
        return
    with notmuch_database(write=True) as db:
        with db.atomic():
            for fp in cur_paths:
                msg = db.get(str(fp.resolve()))
                msg.tags.from_maildir_flags()


def message_paths(query: str, *, limit: int | None = None, sort_newest_first: bool = False) -> list[Path]:
    """Пути сообщений по ``query`` — через изолированный forkserver-reader (см. ``notmuch_query_message_
    paths`` / ``nm_reader_worker``), а НЕ ленивый ``db.messages``-итератор (C++ ``move_to_next`` terminate).
    Retry на discard/SIGABRT — внутри воркер-runner'а (отдельный ``@read_retry`` не нужен)."""
    return notmuch_query_message_paths(query, sort_newest_first=sort_newest_first, limit=limit)


def first_message_path(query: str, *, sort_newest_first: bool = False) -> Path | None:
    paths = message_paths(query, limit=1, sort_newest_first=sort_newest_first)
    return paths[0] if paths else None


def _first_message_path_for_message_id_in_db(
    db: notmuch2.Database, mid: NotmuchMessageIdInner
) -> Path | None:
    msg = first_notmuch_message_for_inner_id(db, mid)  # db.find-first (без move_to_next-краша)
    return Path(msg.path) if msg is not None else None


@read_retry
def first_message_path_for_message_id(mid: NotmuchMessageIdInner) -> Path | None:
    """Find a message path by header Message-ID (db.find single lookup)."""
    with notmuch_database(write=False) as db:
        return _first_message_path_for_message_id_in_db(db, mid)


def first_notmuch_message_for_inner_id(
    db: notmuch2.Database, mid: NotmuchMessageIdInner
) -> notmuch2.Message | None:
    """Первое сообщение в индексе по inner ``Message-ID`` — ТОЛЬКО ``db.find(id)``.

    ``db.find`` = ``notmuch_database_find_message``: одиночный lookup со статус-кодом (error-канал).
    На discard'е ревизии под конкурентной записью поднимает ПИТОНОВСКИЙ ``XapianError`` → ``read_retry``
    переоткрывает; ``LookupError`` = сообщения нет (штатно, напр. orphan IRT). БЕЗ фоллбэка на ленивый
    ``db.messages(q)``-итератор: его ``move_to_next`` (CFFI void, нет error-канала) кидал C++
    ``DatabaseModifiedError`` мимо ``read_retry`` → ``std::terminate`` → SIGABRT. Эмпирически notmuch
    case-sensitive и ``db.find`` ≡ ``id:``-query (надмножество, точный матч спецсимволов без парсера) —
    фоллбэк ничего не находил сверх ``db.find``, был редундантен и крашился (антипаттерн: прямой путь
    обязан работать сам)."""
    try:
        return db.find(mid.value)
    except LookupError as exc:
        log.debug("nm_find_message_absent", mid=mid.value, exc_info=exc)
        return None


def notmuch_index_has_message_id(mid: NotmuchMessageIdInner) -> bool:
    """Union-notmuch уже содержит письмо с данным inner ``Message-ID`` (канонический wire MID).

    Дедуп bridge→ingress и аналогичные проверки: только :class:`~threlium.types.NotmuchMessageIdInner`,
    без голого ``str`` в роли MID (см. ``docs/TYPES.md`` §2).
    """
    return first_message_path_for_message_id(mid) is not None


def notmuch_index_has_message_id_in_db(db: notmuch2.Database, mid: NotmuchMessageIdInner) -> bool:
    """То же, что :func:`notmuch_index_has_message_id`, под уже открытым READ ``db`` (батч мостов)."""
    return first_notmuch_message_for_inner_id(db, mid) is not None


def parent_message_for_in_reply_in_db(
    db: notmuch2.Database, in_reply: RfcInReplyToWire
) -> notmuch2.Message | None:
    """Родитель по ``In-Reply-To`` (present-wire) под уже открытым READ ``db``."""
    mid = NotmuchMessageIdInner.from_present_mid_header_wire(in_reply)
    return first_notmuch_message_for_inner_id(db, mid)  # db.find-first (без move_to_next-краша, см. там)


def thread_id_for_header_message_id_in_db(
    db: notmuch2.Database, mid: NotmuchMessageIdInner
) -> NotmuchThreadScopeId | None:
    """Thread id по inner ``Message-ID`` под уже открытым READ ``db``.

    Через ``first_notmuch_message_for_inner_id`` (``db.find``-first) + ``msg.threadid`` — БЕЗ ленивого
    ``db.messages``-итератора (его ``move_to_next`` под конкурентной записью = C++ SIGABRT, см. там) и
    без лишнего ``db.get(path)``-роундтрипа. ``msg.threadid`` — error-канал'd (Python-исключение →
    ``read_retry``)."""
    msg = first_notmuch_message_for_inner_id(db, mid)
    if msg is None:
        return None
    return NotmuchThreadScopeId.from_notmuch_thread_attr(msg.threadid)


@read_retry
def thread_id_for_header_message_id(mid: NotmuchMessageIdInner) -> NotmuchThreadScopeId | None:
    with notmuch_database(write=False) as db:
        return thread_id_for_header_message_id_in_db(db, mid)


def thread_id_for_optional_message_id(
    mid: NotmuchMessageIdInner | None,
) -> NotmuchThreadScopeId | None:
    """Удобство для FSM: ``None`` Message-ID → ``None`` thread id."""
    if mid is None:
        return None
    return thread_id_for_header_message_id(mid)


@retry(retry=_RETRY_CONDITION, stop=_RETRY_STOP, wait=_RETRY_WAIT, before_sleep=_RETRY_BEFORE_SLEEP, reraise=True)
def batch_tag_add(message_ids: Iterable[NotmuchMessageIdInner], tag: NotmuchTag) -> int:
    """Под одной db.atomic() добавить ``tag`` к списку message-id; idempotent.

    `docs/INDEX.md` §5b.3: ``batch_tag_add(..., NotmuchTag.LIGHTRAG_INDEXED)`` — имя тега
    без ведущего ``+`` (в ``notmuch2`` ``tags.add`` — литерал, не синтаксис CLI ``notmuch tag``).
    Возвращает число успешных проставлений. ``LookupError`` (id отсутствует в
    базе) — проброс наверх.
    """
    mids = list(message_ids)
    wire = tag.value
    n = 0
    with notmuch_database(write=True) as db:
        with db.atomic():
            for mid in mids:
                msg = db.find(mid.value)
                msg.tags.add(wire)
                n += 1
    return n
