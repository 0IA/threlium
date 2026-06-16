"""SUT user systemd pipeline stop/start."""
from __future__ import annotations

from tests.e2e.log import clip_log_body, log
from tests.e2e.sut_user_systemd import (
    e2e_start_all_bridges_bash,
    e2e_start_threlium_user_pipeline_bash,
    e2e_stop_all_bridges_bash,
    e2e_stop_threlium_user_pipeline_bash,
    e2e_sut_threlium_user_journal_rotate_vacuum_bash,
)

from .constants import TIMEOUT_POLL_SHORT
from .runtime import E2EComposeRuntime, service_exec

def e2e_stop_threlium_user_pipeline_services(rt: E2EComposeRuntime) -> None:
    """Остановить на SUT ``threlium-engine`` и активные ``threlium-work@*`` / ``threlium-sweep@*`` (user systemd).

    Вызывается только из координированного preflight pytest перед полным сбросом WireMock/Maildir,
    чтобы не было HTTP к WM без сидированного State.
    """
    completed = service_exec(
        rt.project_name,
        "sut",
        ["bash", "-lc", e2e_stop_threlium_user_pipeline_bash()],
        repo_root=rt.repo_root,
        timeout=int(TIMEOUT_POLL_SHORT),
    )
    if completed.returncode != 0:
        log.warning(
            "sut_pipeline_stop_warning",
            rc=completed.returncode,
            stdout_snippet=(completed.stdout or "")[:800],
        )


def e2e_stop_all_bridges(rt: E2EComposeRuntime) -> None:
    """Остановить ВСЕ мосты на SUT ДО рестарта backends (WireMock/GreenMail) в cold-reset (последовательность)."""
    completed = service_exec(
        rt.project_name,
        "sut",
        ["bash", "-lc", e2e_stop_all_bridges_bash()],
        repo_root=rt.repo_root,
        timeout=int(TIMEOUT_POLL_SHORT),
    )
    log.info("bridges_stopped", detail=(completed.stdout or "").strip()[:600])
    if completed.returncode != 0:
        log.warning("bridges_stop_warning", rc=completed.returncode,
                    stdout_snippet=(completed.stdout or "")[:400])


def e2e_start_all_bridges(rt: E2EComposeRuntime) -> None:
    """Поднять ВСЕ мосты на SUT ПОСЛЕ полной готовности backends + wipe (cold-reset)."""
    completed = service_exec(
        rt.project_name,
        "sut",
        ["bash", "-lc", e2e_start_all_bridges_bash()],
        repo_root=rt.repo_root,
        timeout=int(TIMEOUT_POLL_SHORT),
    )
    log.info("bridges_started", detail=(completed.stdout or "").strip()[:600])
    if completed.returncode != 0:
        log.warning("bridges_start_warning", rc=completed.returncode,
                    stdout_snippet=(completed.stdout or "")[:400])


def e2e_stop_all_sut_services(rt: E2EComposeRuntime) -> None:
    """ЕДИНОЕ API: остановить ВСЕ user-systemd сервисы SUT перед backend-restart/wipe (cold-reset).

    Последовательно (НЕ параллельно — параллельный рестарт backend под живыми сервисами был источником
    гонок: torn lancedb-стор из-под FD движка + краш-сторм мостов на обрыве коннекта):
      1) мосты (``threlium-bridge@*``) — consumers backend'ов, глушим первыми, чтобы не дрались с рестартом;
      2) engine + ``work@`` + ``sweep@`` с барьером смерти (FD на ``lightrag/`` освобождены, Restart подавлен).
    После этого backends можно безопасно рестартовать и делать wipe. Парный старт — :func:`e2e_start_all_sut_services`.
    """
    e2e_stop_all_bridges(rt)
    e2e_stop_threlium_user_pipeline_services(rt)


def e2e_start_all_sut_services(rt: E2EComposeRuntime) -> None:
    """ЕДИНОЕ API: поднять ВСЕ user-systemd сервисы SUT ПОСЛЕ готовности backends + wipe (cold-reset).

    Сначала мосты (не зависят от движка; не падаем, если мост не встал — лог), затем engine (его старт
    обязателен — бросает при сбое). Bootstrap-реиндекс (wait_indexed + идемпотентный restart движка) —
    отдельные шаги ПОСЛЕ этого вызова. Пара к :func:`e2e_stop_all_sut_services`.
    """
    e2e_start_all_bridges(rt)
    e2e_start_threlium_user_pipeline_services(rt)


def e2e_sut_threlium_user_journal_rotate_and_vacuum(rt: E2EComposeRuntime) -> None:
    """Ротация и vacuum user-journal ``threlium`` на SUT (cold reset).

    Вызывать **после** :func:`e2e_stop_threlium_user_pipeline_services`, пока user systemd ещё жив.
    """
    completed = service_exec(
        rt.project_name,
        "sut",
        ["bash", "-lc", e2e_sut_threlium_user_journal_rotate_vacuum_bash()],
        repo_root=rt.repo_root,
        timeout=int(TIMEOUT_POLL_SHORT),
    )
    tail = (completed.stdout or "").strip()
    if tail:
        log.debug(
            "sut_journal_rotate_tail",
            body=clip_log_body(tail, max_len=2000),
        )
    if completed.returncode != 0:
        log.warning(
            "sut_journal_rotate_warning",
            rc=completed.returncode,
            stderr_snippet=(completed.stderr or "")[:600],
        )


def e2e_start_threlium_user_pipeline_services(rt: E2EComposeRuntime) -> None:
    """Запустить ``threlium-engine.service`` на SUT (user systemd) после cold-reset окружения."""
    completed = service_exec(
        rt.project_name,
        "sut",
        ["bash", "-lc", e2e_start_threlium_user_pipeline_bash()],
        repo_root=rt.repo_root,
        timeout=int(TIMEOUT_POLL_SHORT),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "e2e: failed to start threlium-engine.service on SUT after pre-run reset; "
            f"rc={completed.returncode} stdout={(completed.stdout or '')[-1200:]!r}"
        )
