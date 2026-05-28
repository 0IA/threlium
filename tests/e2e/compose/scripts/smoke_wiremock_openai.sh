#!/usr/bin/env bash
# Быстрая проверка, что WireMock из e2e compose отвечает (Admin API).
# Использование (с хоста):
#   tests/e2e/compose/scripts/smoke_wiremock_openai.sh [имя_или_id_контейнера_wiremock]
# Если аргумент не задан — берётся первый контейнер с именем *wiremock*.
set -euo pipefail

CTR="${1:-}"
if [[ -z "${CTR}" ]]; then
  CTR="$(docker ps --filter "name=wiremock" --format '{{.ID}}' | head -1)"
fi
if [[ -z "${CTR}" ]]; then
  echo "No wiremock container found (docker ps --filter name=wiremock)." >&2
  exit 1
fi

map="$(docker port "${CTR}" 8080 2>/dev/null | head -1)"
if [[ -z "${map}" ]]; then
  echo "No port 8080 mapped for ${CTR}" >&2
  exit 1
fi
host_port="${map##*:}"
base="http://127.0.0.1:${host_port}"

echo "Using container ${CTR} -> ${base}"
curl -sf --max-time 5 "${base}/__admin/mappings" >/dev/null
echo "admin /__admin/mappings: OK"
# wiremock-state-extension Admin API (пустой список контекстов — норма)
curl -sf --max-time 5 "${base}/__admin/state-extension/contexts" >/dev/null
echo "admin /__admin/state-extension/contexts: OK"
echo "smoke_wiremock_openai: OK"
