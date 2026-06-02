#!/usr/bin/env bash
# «Золотой» bake образа SUT для ускорения mailflow e2e: тот же compose + тот же site.yml, что в тестах, затем docker commit.
#
# Требования: Docker, ansible-playbook на хосте, репозиторий с ansible/ как сейчас.
#
# Переменные окружения (опционально):
#   THRELIUM_E2E_BAKE_PROJECT     — имя compose-проекта (по умолчанию threlium_e2e_bake)
#   THRELIUM_E2E_BAKE_IMAGE       — тег результирующего образа (по умолчанию threlium/e2e-sut:baked; см. tests/e2e/toolkit/constants.py)
#   THRELIUM_E2E_SUT_BOOTSTRAP_IMAGE — базовый образ для `compose up` при bake (по умолчанию geerlingguy/docker-ubuntu2404-ansible:latest), чтобы не зависеть от уже собранного baked-тега

#
# Bake: полный site.yml под ansible-e2e.cfg. Чистка harness (never+refresh) при обычном прогоне не выполняется.
# Тег refresh для узкого harness — wipe_sync / pytest с ``--tags refresh``.
#
# Политика контейнеров: в начале скрипта — compose down этого проекта (снять хвост прошлого прогона).
# При ошибке ansible или commit контейнеры **не** снимаются (удобно для отладки). Следующий запуск
# bake снова вызовет cleanup в начале; pytest при preflight снимет все проекты из каталога compose e2e.
#
# После успеха:
#   export THRELIUM_E2E_SUT_IMAGE=<тот же тег>  # только если меняли THRELIUM_E2E_BAKE_IMAGE
#   pytest -vv -s                                # все e2e-тесты на свежезапечённом образе
#
# Обычному пользователю этот скрипт руками запускать не нужно: `pytest -n0 tests/e2e/wipe_bake.py`
# (или `THRELIUM_E2E_REBUILD_BAKED_IMAGE=1` в сессии с тестами) вызывает bake под тем же локом, что compose (см. docs/TESTING.md §3, §5).
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
COMPOSE_DIR="${REPO_ROOT}/tests/e2e/compose"
COMPOSE_FILE="${COMPOSE_DIR}/docker-compose.yml"

PROJECT="${THRELIUM_E2E_BAKE_PROJECT:-threlium_e2e_bake}"
IMAGE_TAG="${THRELIUM_E2E_BAKE_IMAGE:-threlium/e2e-sut:baked}"
BOOTSTRAP_IMAGE="${THRELIUM_E2E_SUT_BOOTSTRAP_IMAGE:-geerlingguy/docker-ubuntu2404-ansible:latest}"

cleanup() {
  if [[ -n "${PROJECT:-}" ]]; then
    THRELIUM_E2E_SUT_IMAGE="${BOOTSTRAP_IMAGE}" docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" down --remove-orphans --volumes 2>/dev/null || true
  fi
}

# Снять остаток прошлого bake (прерванный / упавший ansible) перед новым стартом.
# При ошибке в этом прогоне стек **не** снимаем — остаётся для docker exec / логов;
# следующий bake или preflight pytest (preflight compose-каталога) уберут проект.
cleanup

echo "[bake] repo root: ${REPO_ROOT}"
echo "[bake] compose up project=${PROJECT} sut_base=${BOOTSTRAP_IMAGE} -> commit ${IMAGE_TAG}"
THRELIUM_E2E_SUT_IMAGE="${BOOTSTRAP_IMAGE}" docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" up -d

SUT_ID="$(THRELIUM_E2E_SUT_IMAGE="${BOOTSTRAP_IMAGE}" docker compose -f "${COMPOSE_FILE}" -p "${PROJECT}" ps -q sut)"
if [[ -z "${SUT_ID}" ]]; then
  echo "[bake] error: empty sut container id" >&2
  exit 1
fi
echo "[bake] sut container: ${SUT_ID}"

echo "[bake] ansible-playbook site.yml (e2e inventory + ansible-e2e.cfg)"
(
  cd "${REPO_ROOT}/ansible"
  export ANSIBLE_CONFIG="${REPO_ROOT}/ansible/ansible-e2e.cfg"
  mkdir -p collections
  if [[ ! -f collections/ansible_collections/community/docker/plugins/connection/docker.py ]] \
     || [[ ! -f collections/ansible_collections/community/general/plugins/modules/archive.py ]]; then
    ansible-galaxy collection install -r collections/requirements.yml -p collections --force
  fi
  ansible-playbook playbooks/site.yml \
    -i inventory/e2e/hosts.yml \
    -e "e2e_sut_container_id=${SUT_ID}"
)

echo "[bake] docker commit -> ${IMAGE_TAG}"
docker commit "${SUT_ID}" "${IMAGE_TAG}"

cleanup

echo "[bake] done. Example:"
echo "  export THRELIUM_E2E_SUT_IMAGE=${IMAGE_TAG}   # only if you changed THRELIUM_E2E_BAKE_IMAGE"
echo "  pytest -vv -s                                # runs all e2e tests on the freshly baked image"
