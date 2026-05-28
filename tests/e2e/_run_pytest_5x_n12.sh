#!/usr/bin/env bash
# Пять последовательных прогонов e2e (pyproject: testpaths = tests/e2e).
# Весь вывод в один лог-файл (append к одному fd после truncate), без гонки двух tee на один путь.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT" || exit 1
# shellcheck source=/dev/null
. .venv/bin/activate
LOG="${1:-/tmp/threlium-pytest-5x-n12-$(date +%Y%m%d%H%M%S).log}"
: >"$LOG"
exec >>"$LOG" 2>&1
echo "log: $LOG"
fail=0
for run in 1 2 3 4 5; do
  echo ""
  echo "========== PYTEST RUN $run/5 $(date -Iseconds) =========="
  set +e
  pytest -n 12 -s --tb=short
  ec=$?
  set -e
  echo "========== RUN $run/5 pytest exit=$ec =========="
  if [ "$ec" -ne 0 ]; then
    fail=1
  fi
done
echo ""
echo "========== ALL FIVE RUNS DONE $(date -Iseconds) any_fail=$fail =========="
exit "$fail"
