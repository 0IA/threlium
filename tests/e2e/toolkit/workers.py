"""SUT worker idle waits."""
from __future__ import annotations

import os
import shlex
from pathlib import Path

from tests.e2e.log import clip_log_body, log
from tests.e2e.sut_user_systemd import (
    E2E_THRELIUM_USER,
    e2e_sut_threlium_user_workers_idle_probe_bash,
    e2e_sut_threlium_user_workers_stall_diag_bash,
)

from .constants import E2E_SUT_NOTMUCH_BASH_EXPORT, REPO_ROOT, TIMEOUT_POLL_SHORT
from .poll import poll_until_backoff
from .runtime import service_exec

def _e2e_log_sut_workers_stall_diag(project_name: str, *, repo_root: Path, banner: str) -> None:
    """Снимок SUT при таймауте ``wait_for_sut_threlium_user_workers_idle`` (list-units + journal)."""
    r = service_exec(
        project_name,
        "sut",
        ["bash", "-lc", e2e_sut_threlium_user_workers_stall_diag_bash()],
        repo_root=repo_root,
        timeout=int(TIMEOUT_POLL_SHORT),
    )
    body = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
    cap = 25_000
    if len(body) > cap:
        body = body[:cap] + "\n… (truncated)"
    log.debug(
        "sut_workers_stall_diag",
        banner=banner,
        body=clip_log_body(body, max_len=cap),
    )


def wait_for_sut_threlium_user_workers_idle(
    project_name: str,
    *,
    repo_root: Path | None = None,
    timeout: float = TIMEOUT_POLL_SHORT,
) -> None:
    """Дождаться отсутствия активных user-unit ``threlium-work@*`` и ``threlium-sweep@*``.

    Нужно перед mailflow на живом SUT: иначе долетающие LiteLLM со старым ``X-Threlium-Route``
    дают unmatched после холодного прогона или до ``wiremock_state_reset_all_contexts`` в ``pytest_sessionfinish``.

    При ``TimeoutError`` в лог уходит :func:`~tests.e2e.sut_user_systemd.e2e_sut_threlium_user_workers_stall_diag_bash`.

    Под pytest-xdist (``-n>=2``) глобальный idle НЕДОСТИЖИМ: соседний тест держит ``threlium-work@``
    занятыми. Изоляция теста при этом обеспечивается marker-scoped чисткой своих тредов + thread-root
    корреляцией (+ glue-wait между ходами multiturn), а НЕ глобальным idle. Поэтому под xdist ожидание —
    короткий best-effort: дожидаемся idle, если он быстро настанет, иначе логируем и идём дальше (без падения).
    """
    root = repo_root or REPO_ROOT
    script = e2e_sut_threlium_user_workers_idle_probe_bash()

    def _probe() -> bool | None:
        r = service_exec(
            project_name,
            "sut",
            ["bash", "-lc", script],
            repo_root=root,
            timeout=int(TIMEOUT_POLL_SHORT),
        )
        if r.returncode != 0:
            return None
        try:
            line = (r.stdout or "").strip().splitlines()[-1]
            n = int(line)
        except (ValueError, IndexError):
            return None
        return True if n == 0 else None

    under_xdist = bool(os.environ.get("PYTEST_XDIST_WORKER"))
    effective_timeout = min(timeout, 12.0) if under_xdist else timeout
    try:
        poll_until_backoff(
            _probe,
            timeout=effective_timeout,
            desc="sut: threlium-work@ / threlium-sweep@ idle (user systemd)",
        )
    except TimeoutError as e:
        if under_xdist:
            log.warning(
                "sut_workers_idle_wait_best_effort_xdist",
                note="global idle unreachable under -n>=2 (concurrent test busy); proceeding "
                "(isolation via marker-scoped cleanup + thread-root, not global idle)",
                timeout_sec=effective_timeout,
            )
            return
        _e2e_log_sut_workers_stall_diag(
            project_name,
            repo_root=root,
            banner=f"sut workers idle TIMEOUT diag (timeout={timeout}s): {e}",
        )
        raise


def _e2e_drain_thread_registry_path() -> str:
    """Per-PROCESS файл inner Message-ID писем текущего теста (скоуп teardown-drain по их тредам)."""
    return f"/tmp/e2e_drain_threads_{os.getpid()}.txt"


def e2e_record_test_drain_thread(nm_inner: str) -> None:
    """Зарегистрировать inner Message-ID письма теста → per-test teardown-drain ждёт слива ТОЛЬКО его
    notmuch-треда, а не глобального ``tag:unread``.

    Зачем: под ``-n>=2`` общий notmuch всегда держит ``unread`` соседних тестов, поэтому ГЛОБАЛЬНЫЙ
    drain-барьер недостижим → проходил best-effort за 12s БЕЗ слива → стабы теста выгружались
    mid-pipeline → следующий LLM-вызов не матчится → 500 → re-dispatch → шторм ``unmatched`` (см.
    [[residual-triage-retry-storm-finding]], [[per-test-triage-drain-barrier]]). Скоуп по треду делает
    барьер per-test-ДОСТИЖИМЫМ и под xdist (своё письмо доходит до ``archive``). Файл per-PID — как
    scenario-stub-реестр (:func:`tests.e2e.wiremock_client._e2e_record_scenario_stub_ids`): регистратор
    и teardown-дренер могут оказаться в разных module-объектах под pytest."""
    inner = (nm_inner or "").strip()
    if not inner:
        return
    try:
        with open(_e2e_drain_thread_registry_path(), "a") as f:
            f.write(inner + "\n")
    except OSError as exc:  # pragma: no cover
        log.warning("drain_thread_record_failed", err=repr(exc))


def _e2e_take_drain_threads() -> list[str]:
    """Прочитать+очистить per-test реестр inner-MID (teardown-дренер зовёт РОВНО раз за тест)."""
    path = _e2e_drain_thread_registry_path()
    try:
        with open(path) as f:
            inners = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
    except FileNotFoundError:
        return []
    try:
        os.remove(path)
    except OSError:  # pragma: no cover
        pass
    return inners


def e2e_wait_fsm_and_index_drained(
    project_name: str,
    *,
    repo_root: Path | None = None,
    timeout: float = TIMEOUT_POLL_SHORT,
) -> bool:
    """Универсальный барьер финализации ПЕРЕД выгрузкой стабов теста (docs/E2E.md §6.4).

    Ждём, пока продукт полностью доработал по письмам теста — через **notmuch** (универсально для
    ВСЕХ каналов email/telegram/matrix/isomorph: все финализируются ``egress_self_archive`` →
    письмо доходит до терминальной стадии ``archive``):

    1. ``tag:unread AND NOT folder:archive/Maildir`` == 0 — ни одного письма в обработке (``tag:unread``
       = «требует стадии», диспетчер ре-фалбэчит именно по нему; на терминале он снимается). Значит
       финальный egress отправлен и **новых LLM-вызовов не будет**.
    2. :func:`~threlium.lightrag_drain_query.lightrag_drain_pending_search` == 0 — индексатор слит
       (всё graph-worthy помечено ``lightrag_indexed``/``skipped``/``summarized``).

    Само-исцеляющийся: барьер стоит ДО выгрузки, стабы теста ещё на месте → недозавершённый пайплайн
    доходит сам (тогда ``tag:unread`` снимается). Без барьера выгрузка обрывала бы FSM/индексатор на
    лету → unmatched 500 → застрявший ``tag:unread`` → ре-диспатч-шторм.

    Возвращает ``True`` если слилось, ``False`` если за таймаут НЕ слилось (``-n0``). Под xdist —
    best-effort: всегда ``True`` (соседний тест держит общий notmuch; изоляция — по thread-root).

    ⚠️ **Таймаут слива (``False``) — ВСЕГДА признак бага, других вариантов НЕТ.** Барьер само-
    исцеляющийся (стабы на месте) → если за бюджет полного пайплайна письмо НЕ дошло до ``archive`` /
    индекс НЕ слился, значит пайплайн структурно НЕ может завершиться: дыра в стабах (вызов LLM не
    матчится → 500 → застревание), либо реальный баг продукта/моста. Это НЕ «нагрузка»/«долго» —
    нужно ГЛУБОКО расследовать каждый таймаут (см. [[timeouts-mean-hidden-bug]]). Diag-отчёт
    (:func:`e2e_fsm_pending_diag` + unmatched-тела) даёт точку входа в расследование.
    """
    from threlium.lightrag_drain_query import lightrag_drain_pending_search  # noqa: PLC0415

    root = repo_root or REPO_ROOT
    pending_index_q = lightrag_drain_pending_search()
    scope_inners = _e2e_take_drain_threads()
    fsm_q = "tag:unread AND NOT folder:archive/Maildir"
    if scope_inners:
        # THREAD-SCOPED: резолвим notmuch-тред каждого inner-MID теста → ограничиваем ОБА запроса этими
        # тредами. Достижимо и под xdist (своё письмо доходит до archive), в отличие от глобального unread.
        ids_csv = " ".join(shlex.quote(f"id:{i}") for i in scope_inners)
        script = (
            f"{E2E_SUT_NOTMUCH_BASH_EXPORT}; "
            f"fq={shlex.quote(fsm_q)}; pq={shlex.quote(pending_index_q)}; "
            f"scope=''; for q in {ids_csv}; do "
            'tid=$(notmuch search --output=threads "$q" 2>/dev/null | head -1); '
            '[ -n "$tid" ] && scope="$scope${scope:+ or }$tid"; done; '
            'if [ -n "$scope" ]; then sc=" and ( $scope )"; else sc=""; fi; '
            'fsm=$(notmuch count "$fq$sc"); idx=$(notmuch count "$pq$sc"); '
            'echo "$fsm $idx"'
        )
        scoped = True
    else:
        script = (
            f"{E2E_SUT_NOTMUCH_BASH_EXPORT}; "
            f"fsm=$(notmuch count {shlex.quote(fsm_q)}); "
            f"idx=$(notmuch count {shlex.quote(pending_index_q)}); "
            'echo "$fsm $idx"'
        )
        scoped = False

    def _probe() -> bool | None:
        r = service_exec(
            project_name, "sut", ["bash", "-lc", script], repo_root=root, timeout=int(TIMEOUT_POLL_SHORT)
        )
        if r.returncode != 0:
            return None
        try:
            parts = (r.stdout or "").strip().splitlines()[-1].split()
            fsm, idx = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            return None
        return True if (fsm == 0 and idx == 0) else None

    under_xdist = bool(os.environ.get("PYTEST_XDIST_WORKER"))
    # Scoped-drain ограничен ТРЕДОМ этого теста → достижим и под xdist (полный таймаут, реальный исход).
    # Только НЕ-scoped (тест не зарегистрировал тред) под xdist остаётся best-effort: глобальный unread
    # держат соседи, его не дождаться. Регистрируй тред (e2e_record_test_drain_thread), чтобы барьер работал.
    effective_timeout = timeout if scoped else (min(timeout, 12.0) if under_xdist else timeout)
    try:
        poll_until_backoff(
            _probe,
            timeout=effective_timeout,
            desc="sut: FSM drained (no tag:unread in processing) + lightrag index drained — before stub unload",
        )
        return True
    except TimeoutError:
        if under_xdist and not scoped:
            # Под xdist БЕЗ скоупа глобальный слив недостижим (соседний тест занят) → не гейтим, best-effort.
            log.warning(
                "fsm_index_drain_wait_best_effort_xdist",
                note="global drain unreachable under -n>=2 (concurrent test busy); proceeding",
                timeout_sec=effective_timeout,
            )
            return True
        # ⚠️ Таймаут под -n0 = ВСЕГДА баг (дыра в стабах → 500 → застревание, либо баг продукта/моста),
        # НИКОГДА не «просто долго»/«нагрузка». Барьер само-исцеляющийся (стабы на месте) — раз за бюджет
        # полного пайплайна не дошло, пайплайн структурно не завершается. Расследовать ГЛУБОКО (diag ниже).
        return False


def e2e_fsm_pending_diag(project_name: str, *, repo_root: Path | None = None) -> str:
    """Дамп застрявших писем (``tag:unread AND NOT folder:archive``) с АТРИБУЦИЕЙ ПО ТРЕДУ.

    Барьер слива глобален (ждёт ВЕСЬ notmuch-unread), поэтому «чей это тред» по самому факту таймаута
    не видно — здесь даём по каждому застрявшему письму идентифицирующее содержимое, чтобы однозначно
    сопоставить с тестом-источником и починить НУЖНЫЙ сценарий:
      • ``thread`` + стадия (folder), ``From/To`` (стадии FSM), ``Subject``;
      • ``X-Threlium-Route`` / ``X-Threlium-Thread-Root`` / ``X-Threlium-Irt-Hash`` (корреляторы);
      • строки тела с маркером теста (``e2e …`` / ``E2E_MID:`` / ``MARKER``) — прямой указатель на сценарий.
    Это «что застряло» из отчёта о падении teardown-барьера (см. conftest ``_e2e_autouse_runtime``)."""
    from threlium.lightrag_drain_query import lightrag_drain_pending_search  # noqa: PLC0415

    root = repo_root or REPO_ROOT
    pending_index_q = lightrag_drain_pending_search()
    u = E2E_THRELIUM_USER
    script = (
        f"{E2E_SUT_NOTMUCH_BASH_EXPORT}; "
        'Q="tag:unread AND NOT folder:archive/Maildir"; '
        'echo "-- stuck threads (summary) --"; '
        'notmuch search --output=summary --format=text "$Q" 2>/dev/null | head -20; '
        'echo "-- per-message attribution (headers + test marker) --"; '
        'notmuch show --body=true --format=text "$Q" 2>/dev/null '
        '| grep -aiE "^(Subject|From|To|Message-ID|In-Reply-To|X-Threlium-Route|'
        'X-Threlium-Thread-Root|X-Threlium-Irt-Hash):|e2e[ _:-]|E2E_MID|marker" '
        '| head -50; '
        # Эфемерное (idx-сторона drain): какие письма ещё НЕ слиты индексатором (lightrag pending).
        f'echo "-- lightrag index-pending (count + summary) --"; '
        f'notmuch count {shlex.quote(pending_index_q)} 2>/dev/null; '
        f'notmuch search --output=summary --format=text {shlex.quote(pending_index_q)} 2>/dev/null | head -10; '
        # Эфемерное: упавшие work@/sweep@ (краш стадии FSM = главная причина застревания tag:unread).
        'echo "-- failed work@/sweep@ units --"; '
        f'uid=$(id -u {u}); '
        f'runuser -u {u} -- env XDG_RUNTIME_DIR=/run/user/$uid systemctl --user list-units '
        "'threlium-work@*' 'threlium-sweep@*' --state=failed --no-legend 2>/dev/null | head -20"
    )
    try:
        r = service_exec(
            project_name, "sut", ["bash", "-lc", script], repo_root=root, timeout=int(TIMEOUT_POLL_SHORT)
        )
        out = (r.stdout or "").strip()
        return out if out else "(no stuck tag:unread messages — index drain alone timed out)"
    except Exception as exc:  # noqa: BLE001
        return f"(fsm_pending_diag failed: {exc!r})"
