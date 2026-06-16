"""SUT / GreenMail cleanup between scenarios."""
from __future__ import annotations

import imaplib
import os
import shlex

from tests.e2e.log import log
from tests.e2e.sut_user_systemd import E2E_THRELIUM_USER

from .constants import (
    E2E_FETCHMAIL_PASS,
    E2E_FETCHMAIL_USER,
    E2E_GREENMAIL_REPLY_USER,
    E2E_IMAP_PROCESSED_FOLDER,
    E2E_REMOTE_POSIX_HOME,
    E2E_REMOTE_THRELIUM_HOME,
    THRELIUM_E2E_SKIP_SUT_MAILDIR_FLUSH_ENV,
    TIMEOUT_POLL_SHORT,
)
from .greenmail import _greenmail_imap_expunge_folder
from .runtime import E2EComposeRuntime, service_exec
from .sut_fs_cleanup import e2e_sut_remove_paths


def e2e_flush_greenmail_inboxes(rt: E2EComposeRuntime) -> None:
    """EXPUNGE GreenMail IMAP: ``INBOX`` (``test@``, ``pytest@``) и ``Threlium.Processed`` (``test@``).

    Without this, ``threlium-bridge@email`` picks up stale messages from previous
    runs after SUT Maildir/notmuch flush.  The bridge now drops replies whose
    immediate ``In-Reply-To`` parent is missing from the wiped notmuch index
    (``orphan_skip``), so stale replies no longer feed ``irt_chain.py`` and the
    enrich worker no longer enters a restart loop.  Flushing is still required:
    stale root messages would otherwise be re-delivered as duplicates and the
    IMAP UID watermark must be reset between independent test sessions.

    ``Threlium.Processed`` (UID MOVE после fetch) тоже чистится: после wipe
    notmuch мост стартует с ``effective_start=1`` и иначе снова обрабатывает
    всё, что осталось в INBOX или накопилось в processed-папке между сессиями.
    """
    host, port = rt.greenmail_imap_host, rt.greenmail_imap_port
    flush_specs: list[tuple[str, str, tuple[str, ...]]] = [
        (E2E_FETCHMAIL_USER, E2E_FETCHMAIL_PASS, ("INBOX", E2E_IMAP_PROCESSED_FOLDER)),
        (E2E_GREENMAIL_REPLY_USER, E2E_FETCHMAIL_PASS, ("INBOX",)),
    ]
    for user, password, folders in flush_specs:
        try:
            with imaplib.IMAP4(host, port, timeout=int(TIMEOUT_POLL_SHORT)) as imap:
                imap.login(user, password)
                for folder in folders:
                    try:
                        n = _greenmail_imap_expunge_folder(imap, folder)
                        log.info("greenmail_flush", user=user, folder=folder, expunged=n)
                    except Exception as folder_exc:
                        log.warning(
                            "greenmail_flush_folder_skipped",
                            user=user,
                            folder=folder,
                            error=repr(folder_exc),
                        )
                imap.logout()
        except Exception as exc:
            log.warning("greenmail_flush_skipped", user=user, error=repr(exc))


def e2e_flush_sut_fsm_maildirs(rt: E2EComposeRuntime) -> None:
    """Очистить Maildir, notmuch DB и LightRAG на SUT перед тестовой сессией.

    Полный wipe: файлы Maildir, Xapian индекс notmuch, LightRAG storage — всё пересоздаётся
    engine при старте. Без wipe накопленные данные замедляют LightRAG indexing (173MB+, 60s+ на документ).
    """
    raw = os.environ.get(THRELIUM_E2E_SKIP_SUT_MAILDIR_FLUSH_ENV, "")
    if str(raw).strip().lower() in ("1", "true", "yes", "on"):
        log.info("sut_maildir_flush_skipped", env=THRELIUM_E2E_SKIP_SUT_MAILDIR_FLUSH_ENV)
        return
    # Файловые tree-удаления (LightRAG-стор + notmuch Xapian-индекс) — через ЕДИНЫЙ Python-метод с
    # FD-диагностикой (``sut_fs_cleanup.e2e_sut_remove_paths``): он логирует, держит ли кто-то FD на
    # каталог в момент сноса (cold-reset идёт перед тестами и обязан отдать чистый стор; если движок ещё
    # жив и держит FD на ``lightrag/`` — lancedb стартует на огрызке → манифест ``Not found`` → порча
    # тянется в прогон). Раньше это был ``rm -rf`` в bash без структурного лога и без проверки FD.
    e2e_sut_remove_paths(
        rt,
        [
            E2E_REMOTE_THRELIUM_HOME + "/lightrag",  # LightRAG-стор (lancedb+cozo); 173MB+ за прогон
            E2E_REMOTE_THRELIUM_HOME + "/stages/.notmuch",  # notmuch Xapian (stale thread-ids)
        ],
        reason="cold_reset_flush",
    )
    th = shlex.quote(E2E_REMOTE_THRELIUM_HOME)
    nm_cfg = shlex.quote(E2E_REMOTE_POSIX_HOME + "/.notmuch-config")
    home_q = shlex.quote(E2E_REMOTE_POSIX_HOME)
    notmuch_cmd = "export HOME=" + home_q + " NOTMUCH_CONFIG=" + nm_cfg + "; notmuch new"
    su_wrap = shlex.quote(notmuch_cmd)
    script = f"""set -eu
TH={th}
if [ -d "$TH/stages" ]; then
  find "$TH/stages" \\
    \\( -path '*/Maildir/new/*' -o -path '*/Maildir/cur/*' \\
    -o -path '*/Maildir/tmp/*' \\) \\
    -type f ! -name '.*' -delete 2>/dev/null || true
fi
# LightRAG KV/doc-status теперь в Redis (localhost) — чистим вместе с файловым lightrag-каталогом (снят
# выше через e2e_sut_remove_paths), иначе индекс/кэш прошлой сессии переживёт wipe и сломает изоляцию.
# dbsize after = guard: >0 = стор не очистился (engine жив / чужой redis) → видно в sut_maildir_flush_diag.
redis-cli flushall 2>&1 || echo "[e2e] WARN redis flushall FAILED"
echo "[e2e] COLDRESET redis_dbsize_after=$(redis-cli dbsize 2>&1)"
su - {E2E_THRELIUM_USER} -s /bin/bash -c {su_wrap} </dev/null || true
echo "[e2e] SUT flushed: Maildir + lightrag(files+redis) + notmuch DB wiped, notmuch new done"
"""
    completed = service_exec(
        rt.project_name,
        "sut",
        ["bash", "-lc", script],
        repo_root=rt.repo_root,
        timeout=int(TIMEOUT_POLL_SHORT),
    )
    # COLDRESET_DIAG: всегда логируем stdout — diag о dbsize/doc_status до и после flushall (root-cause
    # 533-doc-survives-wipe). Без этого diag тонет в captured-выводе pytest при passed-тесте.
    log.info(
        "sut_maildir_flush_diag",
        rc=completed.returncode,
        stdout=(completed.stdout or "").strip(),
        stderr=(completed.stderr or "").strip(),
    )
    if completed.returncode != 0:
        log.warning(
            "sut_maildir_flush_warning",
            rc=completed.returncode,
            stdout_snippet=(completed.stdout or "")[:600],
        )
