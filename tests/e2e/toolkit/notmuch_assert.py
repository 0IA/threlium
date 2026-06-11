"""Notmuch / LightRAG-index asserts (HTTP state, no docker-exec).

The historical ``docker exec``-backed notmuch assert helpers (``poll_notmuch_positive``,
``poll_notmuch_thread_in_stage_folder``, ``assert_notmuch_thread_stage_message_count_at_least``,
``assert_notmuch_folder_contains_body_token``, ``assert_notmuch_thread_tag_count``,
``assert_notmuch_thread_has_no_unread``) were removed: every test had migrated its routing/stage/tag
checks to WireMock state + GreenMail (E2E.md §3.6), leaving them dead code. ``docker exec`` inside a
test body is an antipattern ([[no-docker-exec-journalctl-in-tests]]). Only the HTTP-state index probe
below remains.
"""
from __future__ import annotations

from pathlib import Path

from .constants import REPO_ROOT, TIMEOUT_POLL_SHORT
from .poll import poll_until


def poll_lightrag_indexed_positive(
    project_name: str,
    *,
    correlation_key: str,
    repo_root: Path | None = None,
    timeout: float | None = None,
) -> None:
    """Wait until LightRAG embedded chunks **for this thread** — via WireMock state, not the global file.

    Индексация целиком завязана на вызовы стабов WireMock: эмбеддинг-стаб сценария (``006_embeddings_*``
    и т.п.) на каждом обслуживании пишет ``recordState`` флаг ``lightrag_embedded`` в контекст,
    ключёванный ЧИСТО по ``X-Threlium-Thread-Root`` (tag-free). Ждём именно этот флаг через probe-стаб
    (HTTP), а не ``docker exec stat`` ГЛОБАЛЬНОГО ``faiss_index_chunks.index.meta.json``.

    Почему так (урок ``-n2``): прежний ``stat`` (a) бил по ОДНОМУ общему faiss-файлу (не изолирован по
    треду), (b) шёл через ``service_exec`` = ``docker exec``, который под ``-n2`` конкурирует/голодает →
    poll выгорал по таймауту, хотя индексация шла (faiss рос). Флаг по thread-root: per-test изоляция,
    дёшево (HTTP к WireMock), без зависимости от объёма журнала/faiss и без ``docker exec``. См. §3.6.
    """
    from tests.e2e.wiremock_client import (  # noqa: PLC0415
        wiremock_public_base,
        wiremock_state_thread_root_call_sites,
    )

    from .runtime import discover_runtime  # noqa: PLC0415

    w = float(timeout) if timeout is not None else float(TIMEOUT_POLL_SHORT)
    rt = discover_runtime(project_name, repo_root=repo_root or REPO_ROOT)
    wm = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)

    def _probe() -> str | None:
        cs = wiremock_state_thread_root_call_sites(wm, correlation_key)
        return "1" if "lightrag_index" in cs else None

    poll_until(
        _probe, timeout=w, interval=2.0, desc="lightrag_index call-site (thread-root state)"
    )
