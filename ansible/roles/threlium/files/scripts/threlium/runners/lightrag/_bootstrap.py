"""Bootstrap-индексация файлов из $THRELIUM_HOME/knowledge/ в LightRAG при старте engine.

Дедупликация — ответственность LightRAG (``apipeline_enqueue_documents`` вызывает
``doc_status.filter_keys`` внутри ``ainsert``). Повторная загрузка при рестарте —
no-op на стороне RAG, без лишних LLM/embed вызовов.
"""
from __future__ import annotations

import hashlib
import logging
from email import policy
from email.message import EmailMessage
from pathlib import Path

from lightrag import LightRAG

from threlium.settings import ThreliumSettings
from threlium.types.lightrag_document_header import LightragDocumentHeader

log = logging.getLogger(__name__)

_ALLOWED_SUFFIXES = frozenset((".md", ".txt", ".ttl", ".json", ".yaml", ".yml"))


def _doc_id_for_path(rel_path: str) -> str:
    h = hashlib.md5(rel_path.encode(), usedforsecurity=False).hexdigest()[:16]
    return f"knowledge:bootstrap:{h}"


def _wrap_as_rfc822(content: str, *, doc_id: str, filename: str) -> str:
    """Wrap raw file content in RFC822 with X-Threlium-Thread-Id for chunking compatibility."""
    msg = EmailMessage()
    msg[LightragDocumentHeader.THREAD_ID] = doc_id
    msg["Subject"] = filename
    msg.set_content(content.rstrip("\n"), subtype="plain", charset="utf-8")
    return msg.as_string(policy=policy.default).strip() + "\n"


async def bootstrap_knowledge_dir(rag: LightRAG, settings: ThreliumSettings) -> None:
    """Index knowledge files; LightRAG deduplicates internally via doc_status."""
    knowledge_dir = Path(settings.home) / "knowledge"
    if not knowledge_dir.is_dir():
        log.info("bootstrap_knowledge: directory not found, skipping: %s", knowledge_dir)
        return

    candidates: list[tuple[str, str, str]] = []
    for path in sorted(knowledge_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix not in _ALLOWED_SUFFIXES:
            continue
        rel = str(path.relative_to(knowledge_dir))
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.warning("bootstrap_knowledge: cannot read %s: %s", path, e)
            continue
        if not content.strip():
            continue
        doc_id = _doc_id_for_path(rel)
        rfc822 = _wrap_as_rfc822(content, doc_id=doc_id, filename=rel)
        candidates.append((rel, doc_id, rfc822))

    if not candidates:
        log.info("bootstrap_knowledge: no eligible files in %s", knowledge_dir)
        return

    await rag.ainsert(
        [rfc822 for _, _, rfc822 in candidates],
        ids=[doc_id for _, doc_id, _ in candidates],
        file_paths=[rel for rel, _, _ in candidates],
    )
    log.info(
        "bootstrap_knowledge: ainsert called for %d candidates from %s",
        len(candidates),
        knowledge_dir,
    )
