"""SUT: user-scope systemd + ``journalctl`` из ``docker exec`` (root).

Обёртки ``runuser`` + ``XDG_RUNTIME_DIR``: без них ``journalctl --user-unit`` смотрит
не тот user journal. Не импортирует ``helpers`` — только stdlib.
"""
from __future__ import annotations

import os
import shlex

E2E_THRELIUM_USER = os.environ.get("THRELIUM_E2E_THRELIUM_USER", "threlium")
E2E_REMOTE_THRELIUM_HOME = os.environ.get(
    "THRELIUM_E2E_REMOTE_THRELIUM_HOME",
    f"/home/{E2E_THRELIUM_USER}/threlium/data",
)

# ``journalctl --user-unit=…`` из ``docker exec`` (root) смотрит user-session **root**;
# сервисы threlium — в user journal UID ``E2E_THRELIUM_USER``.
E2E_THRELIUM_USER_JOURNALCTL_PREFIX = (
    f"runuser -u {E2E_THRELIUM_USER} -- env "
    f"XDG_RUNTIME_DIR=/run/user/$(id -u {E2E_THRELIUM_USER}) journalctl"
)
E2E_THRELIUM_USER_JOURNAL_TRANSPORT_MATCH = "_TRANSPORT=journal"


def e2e_threlium_user_unit_journalctl_bash(
    user_unit: str,
    lines: int,
    *,
    transport_journal: bool = True,
    shell_redirect: str = "2>&1 || true",
    since: str | None = None,
) -> str:
    """Одна bash-команда: ``journalctl`` user-юнита в журнале ``E2E_THRELIUM_USER`` на SUT (exec от root).

    ``user_unit`` передаётся в ``--user-unit`` (имя или шаблон с ``*``). ``shell_redirect`` — хвост
    команды (например ``2>/dev/null`` для ``if … | grep`` без ``|| true``).

    ``transport_journal=True`` (по умолчанию) оставляет только записи с ``_TRANSPORT=journal``
    (сообщения systemd о start/stop). Логи приложения (structlog на stdout → journald
    ``_TRANSPORT=stdout``) для проверок вроде ``bootstrap_knowledge`` задавайте
    ``transport_journal=False``.
    """
    uq = shlex.quote(user_unit)
    n = max(1, int(lines))
    t = f" {E2E_THRELIUM_USER_JOURNAL_TRANSPORT_MATCH}" if transport_journal else ""
    since_opt = f" --since={shlex.quote(since)}" if since else ""
    return f"{E2E_THRELIUM_USER_JOURNALCTL_PREFIX} --user-unit={uq} -n {n}{t}{since_opt} --no-pager {shell_redirect}"


# Стабильные юниты — ``journalctl --user-unit=name``; шаблонные — через :func:`e2e_threlium_user_unit_journalctl_bash`.
E2E_SUT_THRELIUM_USER_UNIT_JOURNAL = f"""
echo '=== journalctl --user-unit threlium-bridge@email.service (as {E2E_THRELIUM_USER}) ==='
{e2e_threlium_user_unit_journalctl_bash("threlium-bridge@email.service", 80)}
echo ''
echo '=== journalctl --user-unit threlium-engine.service (as {E2E_THRELIUM_USER}) ==='
{e2e_threlium_user_unit_journalctl_bash("threlium-engine.service", 80)}
echo ''
echo '=== journalctl --user-unit "threlium-work@*.service" (as {E2E_THRELIUM_USER}) ==='
{e2e_threlium_user_unit_journalctl_bash("threlium-work@*.service", 80)}
echo ''
echo '=== journalctl --user-unit "threlium-sweep@*.service" (as {E2E_THRELIUM_USER}) ==='
{e2e_threlium_user_unit_journalctl_bash("threlium-sweep@*.service", 60)}
"""


def e2e_stop_threlium_user_pipeline_bash() -> str:
    """Bash-скрипт: остановить engine, work, sweep (user systemd на SUT).

    Мосты (``threlium-bridge@*``) НЕ трогаем: их рестарт сломал бы параллельность (общий стек) и
    замедлил старт. Гонку «живой telegram/matrix-мост переинжектит утёкший update в окне сброса»
    закрываем порядком cold-reset, а не остановкой моста: сперва ``wiremock_state_reset_all_contexts``
    (``telegram_updates`` / ``matrix_rooms`` пусты → ``getUpdates``/``/sync`` ничего не отдаёт),
    затем settle на цикл поллинга моста и только потом flush Maildir — см.
    ``conftest._e2e_wiremock_journal_reset_once``.
    """
    u = E2E_THRELIUM_USER
    return f"""set -eu
uid=$(id -u {u})
export XDG_RUNTIME_DIR=/run/user/$uid
runuser -u {u} -- systemctl --user stop threlium-engine.service 2>/dev/null || true
for i in $(seq 1 30); do
  st=$(runuser -u {u} -- systemctl --user is-active threlium-engine.service 2>/dev/null || true)
  if [ "$st" = inactive ] || [ "$st" = failed ] || [ "$st" = unknown ]; then
    break
  fi
  sleep 1
done
# Зависший/failed engine (напр. прерванный job): сбросить состояние, чтобы последующий
# start не упёрся в "Job canceled"/failed.
runuser -u {u} -- systemctl --user reset-failed threlium-engine.service 2>/dev/null || true
# КРИТИЧНО (cold-reset идёт ПЕРЕД тестами и ОБЯЗАН отдать чистый стор): дождаться смерти самого
# ПРОЦЕССА engine, а не только systemd ``inactive``. После SIGABRT/segfault (напр. notmuch
# concurrent-write C++ DatabaseModifiedError, или баг индексатора) systemd репортит ``failed``/
# ``inactive`` РАНЬШЕ, чем ядро дожнёт процесс и закроет его открытые FD на ``lightrag/``. Если
# вызывающий сделает ``rm -rf lightrag`` пока живой процесс держит эти FD — каталог разлинкуется,
# но процесс продолжит писать в осиротевшие inode → lancedb-манифест укажет на удалённый data-файл
# (``LanceError(IO): Not found ...data/*.lance``) и эта порча ПЕРЕЖИВЁТ wipe в следующий прогон.
# Эскалируем до SIGKILL по cgroup юнита и ждём, пока процесса не станет.
for i in $(seq 1 30); do
  pids=$(runuser -u {u} -- pgrep -u "$uid" -f 'python -m threlium.runners.engine$' 2>/dev/null || true)
  [ -z "$pids" ] && break
  runuser -u {u} -- systemctl --user kill -s SIGKILL threlium-engine.service 2>/dev/null || true
  sleep 0.5
done
# КРИТИЧНО (root-fix воскрешения движка): ``systemctl kill -s SIGKILL`` выше = смерть по сигналу → systemd
# видит сбой → ``Restart=always``/``RestartSec=1s`` ПЛАНИРУЕТ рестарт → движок ВОСКРЕСНЕТ через ~1s и снова
# откроет FD на ``lightrag/`` (cozo_graph/data/LOG) ровно когда вызывающий делает ``rm -rf lightrag`` → снос
# из-под живого FD → торн lancedb/cozo-манифест (``Not found ..._versions/data``), порча тянется в прогон
# (подтверждено: journal ``SIGKILL→status=9/KILL→Scheduled restart`` + /proc holder = сам engine). Поэтому
# ОТМЕНЯЕМ запланированный рестарт: ``systemctl stop`` переводит юнит в inactive и снимает pending restart-job
# — движок остаётся МЁРТВ до НАМЕРЕННОГО старта (cold-reset стартует его сам после wipe). Цикл переживает
# гонку «restart выстрелил между kill и stop»: stop → ждём >RestartSec → если воскрес, добиваем и повторяем.
for i in $(seq 1 20); do
  runuser -u {u} -- systemctl --user stop threlium-engine.service 2>/dev/null || true
  runuser -u {u} -- systemctl --user reset-failed threlium-engine.service 2>/dev/null || true
  sleep 1.5
  pids=$(runuser -u {u} -- pgrep -u "$uid" -f 'python -m threlium.runners.engine$' 2>/dev/null || true)
  [ -z "$pids" ] && break
  runuser -u {u} -- systemctl --user kill -s SIGKILL threlium-engine.service 2>/dev/null || true
done
# reset-failed + stop ВСЕХ инстансов work@/sweep@ ОДНИМ glob-вызовом (НЕ через list-units|awk).
# КРИТИЧНО: у FAILED-юнитов `systemctl list-units` первым столбцом печатает маркер '●', поэтому
# awk по первому столбцу давал '●' (а имя юнита — во втором столбце) → `reset-failed ●` = no-op → FAILED
# work@-инстансы НИКОГДА не сбрасывались. При e2e StartLimitIntervalSec их start-rate-limit счётчик
# копился МЕЖДУ сессиями на долгоживущем контейнере → в итоге `systemctl start` отклонялся ("Start
# request repeated too quickly") → reasoning-воркер не запускался → письмо застревало unread → нет
# ответа (флак mock_live). `reset-failed <glob>` сбрасывает failed-состояние И start-limit счётчик у
# ВСЕХ загруженных инстансов разом; `stop <glob>` добивает живые перед wipe. (Проверено на SUT:
# 15 failed → 0, reasoning:NN → inactive/NRestarts=0.)
runuser -u {u} -- systemctl --user reset-failed 'threlium-work@*.service' 'threlium-sweep@*.service' 2>/dev/null || true
runuser -u {u} -- systemctl --user stop 'threlium-work@*.service' 'threlium-sweep@*.service' 2>/dev/null || true
echo "[e2e] SUT user-scope pipeline stopped (engine + work + sweep; bridges left running)"
"""


def e2e_sut_threlium_user_journal_rotate_vacuum_bash() -> str:
    """Bash: ротация и ужатие **user**-журнала ``E2E_THRELIUM_USER`` на SUT (после остановки pipeline).

    Снимает хвост ``journalctl --user-unit`` от прошлых pytest-сессий на долгоживущем контейнере,
    чтобы диагностика e2e не тащила старые ``Failed with result 'exit-code'`` и т.п.
    ``--vacuum-time=1s`` оставляет минимальный хвост по политике journald (см. ``journalctl(1)``).
    """
    u = E2E_THRELIUM_USER
    return f"""set +e
uid=$(id -u {u})
export XDG_RUNTIME_DIR=/run/user/$uid
# User manager must be up (linger/session); cold reset calls this right after pipeline stop.
runuser -u {u} -- env XDG_RUNTIME_DIR=/run/user/$uid journalctl --user --rotate 2>&1
rc_r=$?
runuser -u {u} -- env XDG_RUNTIME_DIR=/run/user/$uid journalctl --user --vacuum-time=1s 2>&1
rc_v=$?
echo "[e2e] SUT user journal (UID $uid): journalctl --user --rotate rc=$rc_r --vacuum-time=1s rc=$rc_v"
exit 0
"""


def e2e_patch_hop_budget_in_threlium_yaml_bash(*, budget_root: int, budget_sub: int) -> str:
    """Патч ``hop.budget_*`` в ``config/threlium.yaml`` на SUT (без ansible)."""
    cfg = shlex.quote(f"{E2E_REMOTE_THRELIUM_HOME}/config/threlium.yaml")
    return f"""set -eu
cfg={cfg}
test -f "$cfg"
sed -i 's/^  budget_root: .*/  budget_root: {int(budget_root)}/' "$cfg"
sed -i 's/^  budget_sub: .*/  budget_sub: {int(budget_sub)}/' "$cfg"
grep -A3 '^hop:' "$cfg" | head -4
"""


def e2e_restart_threlium_engine_bash() -> str:
    """Перезапуск только ``threlium-engine`` (user systemd), bridges не трогаем."""
    u = E2E_THRELIUM_USER
    return f"""set -eu
uid=$(id -u {u})
export XDG_RUNTIME_DIR=/run/user/$uid
runuser -u {u} -- systemctl --user stop threlium-engine.service 2>/dev/null || true
runuser -u {u} -- systemctl --user reset-failed threlium-engine.service 2>/dev/null || true
runuser -u {u} -- systemctl --user start threlium-engine.service
sleep 1
st=$(runuser -u {u} -- systemctl --user is-active threlium-engine.service || true)
echo "[e2e] threlium-engine.service is-active: ${{st}}"
test "$st" = active
"""


def e2e_start_threlium_user_pipeline_bash() -> str:
    """Bash-скрипт: journald без rate limit, старт engine (user systemd).

    Мосты не стартуем здесь: на cold-reset их не останавливали (см.
    :func:`e2e_stop_threlium_user_pipeline_bash`), они уже работают. ``--user`` enabled bridge@*
    поднимаются systemd-ом сами при первом старте контейнера/линджера.
    """
    u = E2E_THRELIUM_USER
    return f"""set -eu
uid=$(id -u {u})
export XDG_RUNTIME_DIR=/run/user/$uid

mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\\nRateLimitIntervalSec=0\\n' > /etc/systemd/journald.conf.d/e2e-no-ratelimit.conf
systemctl restart systemd-journald 2>/dev/null || true

# Дождаться полной остановки после cold-reset stop (иначе start → "Job canceled").
for i in $(seq 1 30); do
  st=$(runuser -u {u} -- systemctl --user is-active threlium-engine.service 2>/dev/null || true)
  if [ "$st" = inactive ] || [ "$st" = failed ] || [ "$st" = unknown ]; then
    break
  fi
  sleep 1
done
# Сбросить failed-состояние перед стартом (идемпотентный рестарт на живом контейнере).
runuser -u {u} -- systemctl --user reset-failed threlium-engine.service 2>/dev/null || true
runuser -u {u} -- systemctl --user start threlium-engine.service
for i in $(seq 1 30); do
  st=$(runuser -u {u} -- systemctl --user is-active threlium-engine.service 2>/dev/null || true)
  if [ "$st" = active ]; then
    break
  fi
  sleep 1
done
st=$(runuser -u {u} -- systemctl --user is-active threlium-engine.service || true)
echo "[e2e] SUT threlium-engine.service is-active: ${{st}}"
test "$st" = active
"""


def e2e_stop_all_bridges_bash() -> str:
    """Bash: остановить ВСЕ ``threlium-bridge@*`` (user systemd) на время cold-reset.

    КАЖДЫЙ мост держит persistent-соединение к backend, который cold-reset РЕСТАРТУЕТ: email → IMAP-IDLE к
    GreenMail (``ssl.SSLEOFError`` при рестарте); telegram → HTTP long-poll ``getUpdates`` к WireMock
    (``httpx.RemoteProtocolError: Server disconnected`` → ``NetworkError``); matrix → ``/sync`` long-poll;
    isomorph → SSE long-hold — все к WireMock. Рестарт backend под живым мостом рвёт коннект → краш +
    ``Restart=always`` краш-сторм в окне рестарта → потерянные события/письма → таймауты тестов. Поэтому
    ПОСЛЕДОВАТЕЛЬНО (как FD-wipe движка): глушим ВСЕ мосты ДО рестарта backends и поднимаем ПОСЛЕ их полной
    готовности+wipe (``e2e_start_all_bridges_bash``). ``systemctl stop`` подавляет ``Restart=always`` → мосты
    остаются мертвы в окне. Краш+рестарт моста сам по себе штатен (см. ``bridges/*.py:run_bridge`` docstrings)
    — здесь лишь убираем e2e-специфичное окно гонки cold-reset↔backend-restart (раньше мосты «оставляли жить»).
    """
    u = E2E_THRELIUM_USER
    return f"""set +e
uid=$(id -u {u})
export XDG_RUNTIME_DIR=/run/user/$uid
for unit in $(runuser -u {u} -- systemctl --user list-units --all 'threlium-bridge@*' --no-legend 2>/dev/null | awk '{{print $1}}'); do
  runuser -u {u} -- systemctl --user stop "$unit" 2>/dev/null || true
  runuser -u {u} -- systemctl --user reset-failed "$unit" 2>/dev/null || true
  echo "[e2e] bridge stopped (cold-reset: before backend restart): $unit"
done
exit 0
"""


def e2e_start_all_bridges_bash() -> str:
    """Bash: поднять ВСЕ enabled ``threlium-bridge@*`` ПОСЛЕ полной готовности backends + wipe (cold-reset).

    Пара к :func:`e2e_stop_all_bridges_bash`: мосты оживают на УЖЕ ГОТОВЫХ backends, без гонки с рестартом
    WireMock/GreenMail/wipe. Список — из enabled unit-files (переживает ``stop``, в отличие от list-units),
    исключая bare-шаблон ``threlium-bridge@.service``.
    """
    u = E2E_THRELIUM_USER
    return f"""set +e
uid=$(id -u {u})
export XDG_RUNTIME_DIR=/run/user/$uid
for unit in $(runuser -u {u} -- systemctl --user list-unit-files 'threlium-bridge@*' --no-legend 2>/dev/null | awk '{{print $1}}' | grep -v '@\\.service$'); do
  runuser -u {u} -- systemctl --user reset-failed "$unit" 2>/dev/null || true
  runuser -u {u} -- systemctl --user start "$unit" 2>/dev/null || true
  st=$(runuser -u {u} -- systemctl --user is-active "$unit" 2>/dev/null || true)
  echo "[e2e] bridge start: $unit is-active=${{st}}"
done
exit 0
"""


def e2e_sut_threlium_user_workers_idle_probe_bash() -> str:
    """Bash-скрипт: stdout — число активных ``threlium-work@*`` / ``threlium-sweep@*`` (последняя строка — число)."""
    u = E2E_THRELIUM_USER
    return f"""
set -e
n=$(runuser -u {u} -- bash -lc 'export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user --no-pager list-units "threlium-work@*" "threlium-sweep@*" \\
  --state=running,activating --no-legend 2>/dev/null | grep -v "^$" | wc -l')
echo "$n"
"""


def e2e_sut_threlium_user_workers_stall_diag_bash() -> str:
    """Bash-скрипт для логов при таймауте idle: кто в ``running``/``failed``, срез юнитов, хвост user-journal."""
    u = E2E_THRELIUM_USER
    return f"""set +e
echo "=== E2E_DIAG workers/sweep RUNNING_OR_ACTIVATING ==="
runuser -u {u} -- bash -lc 'export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user --no-pager list-units "threlium-work@*" "threlium-sweep@*" \\
  --state=running,activating --no-legend 2>&1'

echo ""
echo "=== E2E_DIAG workers/sweep FAILED ==="
runuser -u {u} -- bash -lc 'export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user --no-pager list-units "threlium-work@*" "threlium-sweep@*" \\
  --state=failed --no-legend 2>&1'

echo ""
echo "=== E2E_DIAG threlium-engine + bridges (running/failed, head) ==="
runuser -u {u} -- bash -lc 'export XDG_RUNTIME_DIR=/run/user/$(id -u)
echo -n "engine: "
systemctl --user is-active threlium-engine.service 2>&1
systemctl --user --no-pager list-units "threlium-bridge@*" \\
  --state=running,activating,failed --no-legend 2>&1 | head -n 15'

echo ""
echo "=== E2E_DIAG list-units threlium-* (first 50 lines) ==="
runuser -u {u} -- bash -lc 'export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user --no-pager list-units "threlium-*" --all --no-legend 2>&1 | head -n 50'

echo ""
echo "=== E2E_DIAG user journalctl --user -n 60 ==="
runuser -u {u} -- bash -lc 'export XDG_RUNTIME_DIR=/run/user/$(id -u)
journalctl --user -n 60 --no-pager 2>&1'

echo ""
echo "=== E2E_DIAG threlium-work / sweep journal (user-unit glob, last 40 each) ==="
{e2e_threlium_user_unit_journalctl_bash("threlium-work@*.service", 40)}
echo "---"
{e2e_threlium_user_unit_journalctl_bash("threlium-sweep@*.service", 40)}
"""
