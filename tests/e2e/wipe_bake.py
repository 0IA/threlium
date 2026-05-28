"""Полный цикл e2e: bake SUT-образа, сброс координаторов compose, ``compose down`` / ``up``, post-up.

Не входит в дефолтную коллекцию ``pytest tests/e2e`` (имя файла вне ``test_*.py``).
Запуск вручную или в CI **до** ``wipe_sync.py`` / сценарных тестов:

.. code-block:: bash

   pytest -n0 -vv -s tests/e2e/wipe_bake.py

Использует тот же ``FileLock`` и каталог координаторов, что :func:`compose_stack`
в ``conftest.py`` (:func:`~tests.e2e.helpers.e2e_compose_coord_paths`).
Запекание вызывает ``bake_e2e_sut_image.sh``: полный ``site.yml`` (образ с нуля; чистка ``never+refresh`` при обычном прогоне не выполняется). Тег ``refresh`` для harness — ``wipe_sync.py`` / :func:`~tests.e2e.helpers.run_e2e_site_playbook` с ``ansible_tags="refresh"``.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time

import pytest
from filelock import FileLock

try:
    import testcontainers.compose  # noqa: F401, PLC0415
    from testcontainers.compose import DockerCompose  # noqa: PLC0415
except ImportError:
    DockerCompose = None  # type: ignore[misc, assignment]

from .helpers import (
    COMPOSE_DIR,
    E2E_COMPOSE_FILE_NAME,
    E2E_PROJECT,
    E2E_SUT_IMAGE_ENV,
    TIMEOUT_POLL_SHORT,
    cleanup_stale_bundle_archives,
    compose_down_project,
    discover_runtime,
    e2e_compose_coord_paths,
    e2e_controller_hint_write,
    ensure_e2e_sut_image_exists,
    run_greenmail_host_readiness_probe,
    stop_stale_compose_projects,
    wait_for_wiremock_ready,
)
from tests.e2e.log import log

from .wiremock_client import upsert_wiremock_compose_bootstrap_stubs, wiremock_public_base


def _wipe_bake_prereq_or_fail() -> None:
    if sys.platform != "linux":
        pytest.fail(
            "e2e wipe_bake: Linux required (same as e2e harness).",
            pytrace=False,
        )
    if DockerCompose is None:
        pytest.fail(
            "e2e wipe_bake: install extras: pip install -e '.[e2e]'",
            pytrace=False,
        )
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.fail(
            "e2e wipe_bake: docker not available (need a running Docker daemon).",
            pytrace=False,
        )


@pytest.mark.e2e
def test_wipe_bake_full_cycle() -> None:
    """Bake SUT, очистить координаторы, снять старые проекты, поднять новый shared compose."""
    _wipe_bake_prereq_or_fail()
    lock_path, ready_flag, runtime_json = e2e_compose_coord_paths()
    t0 = time.monotonic()
    log.info("wipe_bake_start")

    with FileLock(str(lock_path)):
        old_pn: str | None = None
        if runtime_json.exists():
            try:
                data = json.loads(runtime_json.read_text())
                pn = data.get("project_name")
                if isinstance(pn, str) and pn.strip():
                    old_pn = pn.strip()
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass
        ready_flag.unlink(missing_ok=True)
        runtime_json.unlink(missing_ok=True)

        # Сначала снять **все** compose-проекты из ``tests/e2e/compose`` (shared, bake-хвосты и т.д.),
        # чтобы после bake оставался ровно один новый ``…_shared_<hex>``.
        cleaned = stop_stale_compose_projects(project_prefix=E2E_PROJECT)
        if cleaned:
            log.info("wipe_bake_preflight_cleanup", removed=",".join(cleaned))
        if old_pn:
            log.info("wipe_bake_compose_down_previous", project_name=old_pn)
            compose_down_project(old_pn)
        removed_bundles = cleanup_stale_bundle_archives()
        if removed_bundles:
            log.info("wipe_bake_bundles_removed", count=removed_bundles)

        sut_image, did_bake = ensure_e2e_sut_image_exists(force_rebuild=True)
        assert did_bake is True
        os.environ[E2E_SUT_IMAGE_ENV] = sut_image

        project_name = f"{E2E_PROJECT}_shared_{secrets.token_hex(3)}"
        os.environ["COMPOSE_PROJECT_NAME"] = project_name
        log.info(
            "wipe_bake_compose_up",
            project_name=project_name,
            elapsed_sec=round(time.monotonic() - t0, 1),
        )
        dc = DockerCompose(
            str(COMPOSE_DIR),
            compose_file_name=E2E_COMPOSE_FILE_NAME,
            pull=False,
            build=False,
        )
        dc.start()
        sut_fresh_bake = True
        runtime_json.write_text(
            json.dumps({"project_name": project_name, "sut_fresh_bake": sut_fresh_bake})
        )
        ready_flag.touch()
        wait_for_wiremock_ready(project_name, timeout=TIMEOUT_POLL_SHORT)
        rt = discover_runtime(project_name)
        wm_base = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
        upsert_wiremock_compose_bootstrap_stubs(wm_base)
        run_greenmail_host_readiness_probe(project_name)
        e2e_controller_hint_write(project_name, runtime_json_path=runtime_json)

    log.info(
        "wipe_bake_done",
        project_name=project_name,
        elapsed_sec=round(time.monotonic() - t0, 1),
    )
