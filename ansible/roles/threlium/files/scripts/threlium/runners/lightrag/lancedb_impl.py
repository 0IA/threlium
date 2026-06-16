"""LanceDB vector storage for LightRAG — MVCC, lock-free, async-native.

Заменяет faiss/Milvus как ``vector_storage``. LanceDB — встраиваемый (файловый, как SQLite для
векторов), Lance-формат даёт **MVCC**: конкурентные чтения+записи безопасны (нет faiss-segfault на
параллельной записи, нет single-process сериализации Milvus Lite), а нативный **async API**
(``connect_async``) НЕ блокирует event-loop (в отличие от синхронного gRPC Milvus, который морозил
единственный rag-loop — см. память ``n4-rag-loop-stall-pyspy``).

Конкурентность — **lock-free MVCC** (главное преимущество LanceDB: конкурентные чтения И записи без
сериализации; probe подтвердил — конкурентные ops на одном handle и с двух коннектов дают 0 ошибок).
``-n12``-деградация с ``lance error: Not found ...chunks.lance/_versions`` оказалась **НЕ проблемой адаптера/
MVCC**, а багом ТЕСТ-ХАРНЕССА: cold-reset ``rm -rf lightrag`` сносил каталог, пока живой движок (воскрешённый
``Restart=always`` после SIGKILL барьера смерти) держал открытый FD на ``cozo_graph/data/LOG`` → осиротевший
inode → торн-стор. Исправлено в харнессе (``tests/e2e/toolkit/sut_fs_cleanup.py`` добивает FD-холдеров перед
rm), НЕ здесь. Поэтому адаптер — чистый lock-free, без ``read_consistency_interval``-override. Опциональный
single-writer лок (``get_namespace_lock``) оставлен аварийным тумблером (``THRELIUM_LANCEDB_WRITE_LOCK=1``;
**по умолчанию ВЫКЛ**). Эмбеддинг — durable на upsert. Все ошибки во всех операциях логируются и
ПРОБРАСЫВАЮТСЯ (пропуска ``Not found`` нет — всплывёт, если возникнет). См. ``lancedb-concurrent-purge-notfound-bug``.

Реестр lightrag-стора (имя→модуль + allowlist) регистрируется в рантайме из threlium-кода
(``_construction._register_lancedb_storage``), без патча вендора.

Схема таблицы (одна на namespace = entities/relationships/chunks):
``id`` (string) · ``vector`` (fixed_size_list<float32>[dim]) · ``__created_at__`` (int64) ·
по строковой колонке на каждый ``meta_field`` (lightrag всегда кладёт ``content`` в meta_fields —
его и эмбеддим). ``query`` возвращает форму как faiss: ``{**meta, id, distance, created_at}``,
где ``distance`` = cosine-similarity (1 − lance ``_distance``), чтобы downstream-ранжирование
lightrag не менялось.
"""
from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa

from lightrag.base import BaseVectorStorage
from lightrag.kg.shared_storage import get_namespace_lock
from lightrag.utils import compute_mdhash_id

from threlium.logutil import logger

log = logger.bind(component="lancedb_store")


def _sql_str(value: Any) -> str:
    """SQL-литерал строки для where/delete-фильтров LanceDB (экранирование одинарных кавычек)."""
    return "'" + str(value).replace("'", "''") + "'"


@dataclass
class LanceDBVectorDBStorage(BaseVectorStorage):
    def __post_init__(self) -> None:
        self._validate_embedding_func()
        kwargs = self.global_config.get("vector_db_storage_cls_kwargs", {})
        cosine = kwargs.get("cosine_better_than_threshold")
        if cosine is not None:
            self.cosine_better_than_threshold = float(cosine)

        working_dir = self.global_config["working_dir"]
        base = os.path.join(working_dir, self.workspace) if self.workspace else working_dir
        self._uri = os.path.join(base, "lancedb")
        self._table_name = self.namespace
        self._dim = int(self.embedding_func.embedding_dim)
        # content — тоже meta_field (lightrag), хранится как колонка; эмбеддим его текст.
        self._meta_cols = sorted(self.meta_fields)
        self._db: Any = None
        self._tbl: Any = None
        # ОПЦИОНАЛЬНЫЙ single-writer лок мутаций — по умолчанию ВЫКЛ (lock-free MVCC, главное преимущество
        # LanceDB). -n12-деградация была багом тест-харнесса (снос lightrag из-под живого FD движка), не
        # адаптера — чинится в харнессе. Лок оставлен лишь аварийным тумблером ``THRELIUM_LANCEDB_WRITE_LOCK=1``.
        self._serialize_writes: bool = os.environ.get("THRELIUM_LANCEDB_WRITE_LOCK", "0") == "1"
        self._storage_lock: Any = None

    def _schema(self) -> pa.Schema:
        fields = [
            pa.field("id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), self._dim)),
            pa.field("__created_at__", pa.int64()),
        ]
        fields.extend(pa.field(mf, pa.string()) for mf in self._meta_cols)
        return pa.schema(fields)

    async def initialize(self) -> None:
        import lancedb  # noqa: PLC0415 — тяжёлый импорт только при реальном старте стора

        os.makedirs(self._uri, exist_ok=True)
        try:
            self._db = await lancedb.connect_async(self._uri)
            names = await self._db.table_names()
            if self._table_name in names:
                self._tbl = await self._db.open_table(self._table_name)
            else:
                self._tbl = await self._db.create_table(self._table_name, schema=self._schema())
        except Exception as e:
            log.error(
                "lancedb_init_failed",
                workspace=self.workspace,
                table=self._table_name,
                uri=self._uri,
                error=repr(e),
            )
            raise
        if self._serialize_writes:
            # опционально: single-writer лок (как faiss/nano) — сериализует мутации namespace в процессе.
            self._storage_lock = get_namespace_lock(self.namespace, workspace=self.workspace)
        log.debug(
            "lancedb_table_ready",
            workspace=self.workspace,
            table=self._table_name,
            uri=self._uri,
        )

    async def finalize(self) -> None:
        self._tbl = None
        self._db = None

    async def _io(self, op: str, coro: Any, **ctx: Any) -> Any:
        """Выполнить I/O LanceDB, СРАЗУ логируя реальное падение (op + контекст) и пробрасывая дальше.

        Симметрично ``cozo_impl._run``: любой будущий сбой стора (схема/IO/тип/embedding) ловится с
        диагностикой, а не молча роняет doc-pipeline под безликим исключением. Контекст (``ctx``) —
        формы/счётчики, без дампа векторов.
        """
        try:
            return await coro
        except Exception as e:
            log.error(
                "lancedb_io_failed",
                workspace=self.workspace,
                op=op,
                table=self._table_name,
                error=repr(e),
                ctx=ctx,
            )
            raise

    def _write_guard(self) -> Any:
        """Контекст для мутаций: single-writer лок, если включён настройкой; иначе no-op (lock-free MVCC)."""
        if self._serialize_writes and self._storage_lock is not None:
            return self._storage_lock
        return contextlib.nullcontext()

    async def index_done_callback(self) -> None:
        # No-op: LanceDB персистит каждую запись (merge_insert) сразу (MVCC, durable) — отложенного
        # буфера/flush нет (в отличие от faiss/nano, которые материализуют индекс здесь).
        return None

    # ---- write (MVCC, lock-free, embed-at-upsert) ----
    def _row(self, doc_id: str, vec: np.ndarray, record: dict[str, Any]) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": str(doc_id),
            "vector": [float(x) for x in vec],
            "__created_at__": int(time.time()),
        }
        for mf in self._meta_cols:
            val = record.get(mf)
            row[mf] = None if val is None else str(val)
        return row

    async def upsert(self, data: dict[str, dict[str, Any]]) -> None:
        if not data:
            return
        ids = list(data.keys())
        contents = [data[i].get("content", "") for i in ids]
        embeddings = np.asarray(await self.embedding_func(contents), dtype=np.float32)
        if embeddings.shape[0] != len(ids):
            raise RuntimeError(
                f"lancedb upsert: embeddings {embeddings.shape[0]} != ids {len(ids)} "
                f"(namespace={self.namespace})"
            )
        rows = [self._row(doc_id, embeddings[i], data[doc_id]) for i, doc_id in enumerate(ids)]
        # merge_insert по ``id`` = upsert (update существующего / insert нового). MVCC: запись
        # сразу durable+видима, без index_done flush. Эмбеддинг выше — ВНЕ лока (он bottleneck,
        # держим конкурентным); под локом только КОММИТ версии (single-writer → нет CommitConflict).
        async with self._write_guard():
            await self._io(
                "upsert",
                self._tbl.merge_insert("id")
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute(rows),
                n=len(rows),
            )

    # ---- read ----
    def _format(self, row: dict[str, Any]) -> dict[str, Any]:
        meta = {mf: row[mf] for mf in self._meta_cols if row.get(mf) is not None}
        return {**meta, "id": row.get("id"), "created_at": row.get("__created_at__")}

    async def query(
        self, query: str, top_k: int, query_embedding: list[float] | None = None
    ) -> list[dict[str, Any]]:
        if self._tbl is None or await self._tbl.count_rows() == 0:
            return []  # пустой стор (тред до индексации) — eventual consistency, без ошибки
        if query_embedding is not None:
            vec = np.asarray(query_embedding, dtype=np.float32).reshape(-1)
        else:
            vec = np.asarray(await self.embedding_func([query]), dtype=np.float32)[0]
        q = await self._tbl.search(vec.tolist())
        rows = await self._io(
            "query", q.distance_type("cosine").limit(top_k).to_list(), top_k=top_k
        )
        results: list[dict[str, Any]] = []
        for row in rows:
            # LanceDB cosine ``_distance`` = 1 − cosine_similarity → восстанавливаем similarity,
            # чтобы порог/ранжирование совпадали с faiss (IndexFlatIP отдавал inner product).
            sim = 1.0 - float(row.get("_distance", 1.0))
            if sim < self.cosine_better_than_threshold:
                continue
            meta = {mf: row[mf] for mf in self._meta_cols if row.get(mf) is not None}
            results.append(
                {
                    **meta,
                    "id": row.get("id"),
                    "distance": sim,
                    "created_at": row.get("__created_at__"),
                }
            )
        return results

    async def get_by_id(self, id: str) -> dict[str, Any] | None:
        if self._tbl is None:
            return None
        rows = await self._io(
            "get_by_id", self._tbl.query().where(f"id = {_sql_str(id)}").to_list(), id=id
        )
        return self._format(rows[0]) if rows else None

    async def get_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        if not ids or self._tbl is None:
            return []
        in_list = ", ".join(_sql_str(i) for i in ids)
        rows = await self._io(
            "get_by_ids", self._tbl.query().where(f"id IN ({in_list})").to_list(), n=len(ids)
        )
        by_id = {str(r.get("id")): self._format(r) for r in rows}
        return [by_id.get(str(i)) for i in ids]

    async def get_vectors_by_ids(self, ids: list[str]) -> dict[str, list[float]]:
        if not ids or self._tbl is None:
            return {}
        in_list = ", ".join(_sql_str(i) for i in ids)
        rows = await self._io(
            "get_vectors_by_ids",
            self._tbl.query().where(f"id IN ({in_list})").to_list(),
            n=len(ids),
        )
        return {
            str(r["id"]): list(r["vector"])
            for r in rows
            if r.get("id") is not None and r.get("vector") is not None
        }

    # ---- delete ----
    async def delete(self, ids: list[str]) -> None:
        if not ids or self._tbl is None:
            return
        where = f"id IN ({', '.join(_sql_str(i) for i in ids)})"
        # delete = логическая запись (deletion vectors), коммит версии ``v+1``. Под single-writer локом
        # конкурентной компакции во время delete нет → гонки версий (``Not found``) быть не должно. Поэтому
        # отдельного пропуска ``Not found`` больше НЕТ: любая ошибка логируется (``_io``) и ПРОБРАСЫВАЕТСЯ —
        # если ``Not found`` всё же возникнет, упадём и увидим (проверка достаточности лока).
        async with self._write_guard():
            await self._io("delete", self._tbl.delete(where), n=len(ids))

    async def delete_entity(self, entity_name: str) -> None:
        await self.delete([compute_mdhash_id(entity_name, prefix="ent-")])

    async def delete_entity_relation(self, entity_name: str) -> None:
        # Только у relationships-vdb есть src_id/tgt_id; для прочих namespace — no-op.
        if self._tbl is None or (
            "src_id" not in self._meta_cols and "tgt_id" not in self._meta_cols
        ):
            return
        name = _sql_str(entity_name)
        # тоже writer (delete по src/tgt) → под single-writer локом, как upsert/delete.
        async with self._write_guard():
            await self._io(
                "delete_entity_relation",
                self._tbl.delete(f"src_id = {name} OR tgt_id = {name}"),
                entity=entity_name,
            )

    async def drop(self) -> dict[str, str]:
        try:
            if self._db is not None:
                names = await self._db.table_names()
                if self._table_name in names:
                    await self._db.drop_table(self._table_name)
                self._tbl = await self._db.create_table(
                    self._table_name, schema=self._schema()
                )
            return {"status": "success", "message": "table dropped"}
        except Exception as e:  # noqa: BLE001 — статус наружу, как у lightrag-сторов
            log.error("lancedb_drop_failed", workspace=self.workspace, error=repr(e))
            return {"status": "error", "message": str(e)}
