"""Сбор ``unified_messages`` для стадии enrich (notmuch + ``EmailMessage``)."""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path

from threlium import nm
from threlium.context_budget import message_in_unified_mail_context
from threlium.logutil import logger
from threlium.settings import ThreliumSettings
from threlium.thread_context_filter import iter_irt_ancestors_filtered
from threlium.mime_reform import email_message_from_path
from threlium.types import (
    FsmStage,
    MailHeaderName,
    NotmuchMessageIdInner,
    NotmuchQueryConnective,
    NotmuchQueryField,
    NotmuchTag,
    RfcMessageIdWire,
)

log = logger.bind(stage="enrich_context")

_HDR = MailHeaderName


def _sort_email_messages_oldest_first(msgs: list[EmailMessage]) -> list[EmailMessage]:
    def _ts(m: EmailMessage) -> float:
        d = m.get(_HDR.DATE)
        if not d:
            return 0.0
        try:
            dt = parsedate_to_datetime(d)
            return float(dt.timestamp()) if dt is not None else 0.0
        except (TypeError, ValueError, OSError):
            return 0.0

    return sorted(msgs, key=_ts)


def _dedupe_mid_key(m: EmailMessage) -> str:
    raw = m.get(_HDR.MESSAGE_ID)
    if raw:
        w = RfcMessageIdWire.parse_present_optional(str(raw))
        if w is not None:
            inner = NotmuchMessageIdInner.from_optional_wire(w)
            if inner is not None:
                return inner.value
    return ""


def trim_prompt_text(text: str, max_chars: int) -> str:
    """Обрезка **с начала** строки при превышении лимита (старое уходит первым)."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[-max_chars:]


def trim_context_text(text: str, max_chars: int) -> str:
    """Единая обрезка контекста enrich/reasoning: хвост, ``max_chars`` из ``enrich.context_max_chars``."""
    return trim_prompt_text(text, max_chars)


@dataclass(frozen=True)
class UnifiedEmailContext:
    """Три бакета unified-контекста, сохраняющие разделение по источнику."""

    all_messages: list[EmailMessage]
    thread_memory_msgs: list[EmailMessage]
    global_memory_msgs: list[EmailMessage]


def _load_paths(paths: list[Path]) -> list[EmailMessage]:
    loaded: list[EmailMessage] = []
    skipped = 0
    for p in paths:
        try:
            loaded.append(email_message_from_path(p))
        except OSError as exc:
            log.warning("load_path_skipped", path=str(p), exc_msg=str(exc))
            skipped += 1
            continue
    if skipped:
        log.warning("load_paths_skipped_total", skipped=skipped, total=len(paths))
    return loaded


def build_unified_email_messages(
    *,
    settings: ThreliumSettings,
    leaf_inner: NotmuchMessageIdInner,
    thread_id: str,
) -> UnifiedEmailContext:
    """Три источника → дедуп по ``Message-ID`` → хронология старые → новые.

    Возвращает :class:`UnifiedEmailContext` с объединённым списком и
    отдельными бакетами ``thread_memory`` / ``global_memory`` для
    гранулярных MIME-частей.
    """
    n_thread = settings.enrich.context_thread_n
    n_tm = settings.enrich.context_thread_memory_n
    n_gm = settings.enrich.context_global_n

    tail_paths = [
        snap.path
        for snap in itertools.islice(
            iter_irt_ancestors_filtered(leaf_inner), n_thread
        )
    ]
    tail_paths_chrono = list(reversed(tail_paths))

    tm_q = NotmuchQueryConnective.join_and(
        NotmuchQueryField.THREAD.term(thread_id),
        NotmuchQueryField.TO.term(FsmStage.THREAD_MEMORY.rfc822_mailbox),
    )
    tm_paths = nm.message_paths(tm_q, limit=n_tm, sort_newest_first=True)
    tm_paths_chrono = list(reversed(tm_paths))

    gm_q = NotmuchQueryField.TO.term(FsmStage.GLOBAL_MEMORY.rfc822_mailbox)
    gm_paths = nm.message_paths(gm_q, limit=n_gm, sort_newest_first=True)
    gm_paths_chrono = list(reversed(gm_paths))

    memory_path_keys: set[str] = set()
    for p in itertools.chain(tm_paths_chrono, gm_paths_chrono):
        memory_path_keys.add(str(p.resolve()))

    summarized_q = NotmuchQueryConnective.join_and(
        NotmuchQueryField.THREAD.term(thread_id),
        NotmuchTag.CONTEXT_SUMMARIZED.as_tag_query_term(),
    )
    summarized_path_keys: set[str] = {
        str(p.resolve()) for p in nm.message_paths(summarized_q, limit=None, sort_newest_first=False)
    }

    ordered_paths: list[Path] = []
    seen: set[str] = set()
    for p in tail_paths_chrono:
        key = str(p.resolve())
        if key in seen or key in memory_path_keys or key in summarized_path_keys:
            continue
        seen.add(key)
        ordered_paths.append(p)

    loaded: list[EmailMessage] = []
    for p in ordered_paths:
        try:
            loaded.append(email_message_from_path(p))
        except OSError as exc:
            log.warning("unified_load_path_skipped", path=str(p), exc_msg=str(exc))
            continue

    by_mid: dict[str, EmailMessage] = {}
    for m in loaded:
        if not message_in_unified_mail_context(m):
            continue
        k = _dedupe_mid_key(m) or f"__noid_{id(m)}"
        if k not in by_mid:
            by_mid[k] = m

    return UnifiedEmailContext(
        all_messages=_sort_email_messages_oldest_first(list(by_mid.values())),
        thread_memory_msgs=_sort_email_messages_oldest_first(_load_paths(tm_paths_chrono)),
        global_memory_msgs=_sort_email_messages_oldest_first(_load_paths(gm_paths_chrono)),
    )
