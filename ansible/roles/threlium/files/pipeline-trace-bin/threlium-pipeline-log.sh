#!/bin/bash
# Rolling pipeline trace: syslog + append to $THRELIUM_PIPELINE_TRACE_FILE (default /tmp).
set -eu
TRACE_FILE="${THRELIUM_PIPELINE_TRACE_FILE:-/tmp/threlium-pipeline.trace.log}"
MONO=$(python3 -c "import time; print(f\"{time.clock_gettime(time.CLOCK_MONOTONIC):.9f}\")")
WALL=$(date -Is)
PHASE="${1:-unknown}"
shift || true
DETAIL="$*"
LINE="mono=$MONO wall=$WALL phase=$PHASE $DETAIL"
logger -t threlium-pipeline "$LINE"
(
  umask 022
  flock 200
  printf '%s\n' "$LINE" >> "$TRACE_FILE"
) 200>>"${TRACE_FILE}.lock"
