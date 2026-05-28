#!/bin/bash
# Снимок полной трассировки: rolling-файл + отфильтрованный journald за окно времени.
# Использование: threlium-pipeline-collect.sh [output_file]
# Переменные: THRELIUM_PIPELINE_TRACE_FILE, THRELIUM_PIPELINE_COLLECT_SINCE (напр. -45min)
set -eu
OUT="${1:-/tmp/threlium-pipeline-full.trace.log}"
SINCE="${THRELIUM_PIPELINE_COLLECT_SINCE:--60min}"
TRACE_FILE="${THRELIUM_PIPELINE_TRACE_FILE:-/tmp/threlium-pipeline.trace.log}"
{
  echo ""
  echo "======== threlium-pipeline-collect $(date -Is) ========"
  echo "--- rolling file: ${TRACE_FILE} (tail 8000 lines) ---"
  tail -n 8000 "$TRACE_FILE" 2>/dev/null || echo "(no rolling trace file yet)"
  echo ""
  echo "--- journal filtered: _UID=threlium since ${SINCE} ---"
  _U="$(getent passwd threlium 2>/dev/null | cut -d: -f3)"
  _U="${_U:-1000}"
  journalctl _UID="$_U" -S "$SINCE" --no-pager 2>&1 | grep -E \
    'threlium-pipeline|PIPELINE_TRACE|threlium-mf-nm|threlium-unit-trace|\[threlium stage-run|threlium-work-|threlium-bridge-email|threlium-engine\[|Starting Threlium FSM worker|engine_submit_|dispatch_|hook_post_insert|python_trace' \
    | tail -n 12000 || true
} >> "$OUT"
echo "Appended snapshot to $OUT" >&2
