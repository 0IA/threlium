"""Идемпотентный полный ``site.yml`` на ``sut`` при поднятом shared compose.

Не входит в дефолтную коллекцию ``pytest tests/e2e`` (имя файла вне ``test_*.py``).
Запуск после ``wipe_bake.py`` или когда стек уже поднят сценарными тестами:

.. code-block:: bash

   pytest -n0 -vv -s tests/e2e/wipe_sync.py

Требует фикстуру ``compose_stack`` (поднимает стек, если ещё не поднят).
Только тег ``refresh``: в ``site.yml`` — ``deploy``+``refresh`` (код, ``env``, шаблоны; **без** ``pip``); в ``refresh.yml`` — ``never``+``refresh`` (чистка и рестарт user-units). Зависимости/venv — полный ``site.yml`` / **deploy**.
Полный ``deploy`` — отдельный прогон ``site.yml`` / bake / FSTS; здесь не вызывается.
"""
from __future__ import annotations

import pytest

from .helpers import REPO_ROOT, run_e2e_site_playbook


@pytest.mark.e2e
@pytest.mark.e2e_live
def test_wipe_sync_site_playbook_full(compose_stack) -> None:
    """Только harness refresh на ``sut`` (``--tags refresh``)."""
    run_e2e_site_playbook(
        compose_stack.project_name,
        checkout="/unused",
        repo_root=REPO_ROOT,
        ansible_tags="refresh",
    )
