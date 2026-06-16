"""Единый метод удаления файлов/каталогов на SUT — Python (не bash), со структурным логированием.

Раньше удаление было размазано по bash-скриптам через ``docker exec`` (``rm -rf $TH/lightrag``,
``rm -rf $TH/stages/.notmuch``, ``rm -rf $KN`` в [cleanup.py]/[knowledge.py]) — разный API, без
структурного лога, без проверки «кто держит FD». Это и собрано здесь в ОДИН метод.

Ключевая диагностика (помогает ловить cold-reset-vs-live-engine гонку, из-за которой LanceDB ловил
``Not found: _versions``/``Permission denied`` — снос каталога под открытыми FD живого движка): ПЕРЕД
удалением сканируем ``/proc/*/fd`` и логируем, какие процессы держат файлы внутри удаляемого пути.
Непустой ``fd_holders_before`` = удаляем стор/индекс из-под живого процесса → порча манифеста.

Выполняется ВНУТРИ SUT (``service_exec`` → ``python3 -c``), stdlib-only (без psutil/lsof); вывод —
одна JSON-строка, которую хост парсит и кладёт в структурный лог по записи на путь.
"""
from __future__ import annotations

import json

from tests.e2e.log import log

from .runtime import E2EComposeRuntime, service_exec
from .constants import TIMEOUT_POLL_SHORT

# In-SUT stdlib script: для каждого пути — кто держит FD (скан /proc/*/fd); если держат — это leftover
# (cold-reset идёт когда всё должно быть мертво), эскалируем SIGKILL и ЖДЁМ освобождения ДО rm (иначе
# снос под живым FD = осиротевший inode → торн cozo/lancedb-стор → ``_versions Not found`` в прогон).
# Затем rmtree/remove, проверка выживания. argv[1:] = пути. Печатает ОДНУ json-строку. Без сторонних deps.
_SUT_RM_SCRIPT = r"""
import json, os, shutil, signal, sys, time

def fd_holders(target):
    target = os.path.realpath(target)
    mypid = str(os.getpid())
    holders = []
    try:
        pids = os.listdir('/proc')
    except OSError:
        return holders
    for pid in pids:
        if not pid.isdigit() or pid == mypid:
            continue
        fddir = '/proc/' + pid + '/fd'
        try:
            fds = os.listdir(fddir)
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(fddir + '/' + fd)
            except OSError:
                continue
            if link == target or link.startswith(target + '/'):
                try:
                    comm = open('/proc/' + pid + '/comm').read().strip()
                except OSError:
                    comm = '?'
                holders.append({'pid': pid, 'comm': comm, 'fd': link})
                break
    return holders

def wait_no_holders(path, deadline=8.0):
    # Эскалация: SIGKILL всех держащих FD leftover-процессов, ждём освобождения до дедлайна.
    killed = []
    t0 = time.time()
    h = fd_holders(path)
    while h and (time.time() - t0) < deadline:
        for x in h:
            if x['pid'] not in killed:
                try:
                    os.kill(int(x['pid']), signal.SIGKILL)
                    killed.append(x['pid'])
                except (ProcessLookupError, PermissionError, ValueError):
                    pass
        time.sleep(0.2)
        h = fd_holders(path)
    return killed, h  # killed pids, remaining holders (empty = success)

out = []
for p in sys.argv[1:]:
    rec = {'path': p, 'existed': os.path.exists(p)}
    if rec['existed']:
        holders = fd_holders(p)
        rec['fd_holders_before'] = holders
        if holders:
            killed, remaining = wait_no_holders(p)
            rec['fd_holders_killed'] = killed
            rec['fd_holders_remaining'] = remaining
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
            rec['removed'] = True
        except FileNotFoundError:
            rec['removed'] = True
        except Exception as e:
            rec['removed'] = False
            rec['error'] = repr(e)
        rec['survived'] = os.path.exists(p)
        if rec['survived']:
            rec['fd_holders_after'] = fd_holders(p)
    out.append(rec)
print(json.dumps(out))
"""


def e2e_sut_remove_paths(
    rt: E2EComposeRuntime,
    paths: list[str],
    *,
    reason: str,
    timeout: float = TIMEOUT_POLL_SHORT,
) -> list[dict]:
    """Удалить пути на SUT через ОДИН логируемый Python-путь (rmtree для каталогов, remove для файлов).

    Возвращает список записей ``{path, existed, removed, survived, fd_holders_before/after, error}``.
    Логирует каждую запись: ``sut_rm`` (норма) либо ``sut_rm_survived_fds_held`` (каталог пережил
    удаление — обычно живой процесс держит FD: это и есть искомая гонка cold-reset↔engine).
    """
    if not paths:
        return []
    argv = ["python3", "-c", _SUT_RM_SCRIPT, *paths]
    completed = service_exec(rt.project_name, "sut", argv, repo_root=rt.repo_root, timeout=int(timeout))
    stdout = (completed.stdout or "").strip()
    try:
        records = json.loads(stdout.splitlines()[-1]) if stdout else []
    except (ValueError, IndexError) as e:
        log.warning(
            "sut_rm_parse_failed",
            reason=reason,
            rc=completed.returncode,
            stdout=stdout[:600],
            error=repr(e),
        )
        return []
    for rec in records:
        holders_before = rec.get("fd_holders_before") or []
        remaining = rec.get("fd_holders_remaining") or []
        if rec.get("survived"):
            # Каталог пережил rm — обычно держатель FD так и не освободил → стор стартует на огрызке.
            log.error(
                "sut_rm_survived_fds_held",
                reason=reason,
                path=rec.get("path"),
                fd_holders=rec.get("fd_holders_after") or remaining or holders_before,
                error=rec.get("error"),
            )
        elif holders_before:
            # Был leftover-держатель FD на момент сноса — мы его дождались/добили (SIGKILL) ДО rm, так что
            # снос безопасен; но факт leftover'а диагностически важен (барьер смерти его пропустил).
            log.warning(
                "sut_rm_killed_fd_holders_before_rm",
                reason=reason,
                path=rec.get("path"),
                fd_holders=holders_before,
                killed=rec.get("fd_holders_killed"),
                remaining=remaining,
            )
        else:
            log.info(
                "sut_rm",
                reason=reason,
                path=rec.get("path"),
                existed=rec.get("existed"),
                removed=rec.get("removed"),
            )
    return records
