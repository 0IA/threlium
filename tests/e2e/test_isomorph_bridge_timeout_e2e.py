"""E2e: upstream-timeout → **504**. Мост отдаёт 504, если egress-push не пришёл за ``request_timeout_sec``.

**Серийный тест** (skip под xdist): чтобы не ждать дефолтные 180c, временно понижает ``request_timeout_sec``
моста до 8c (env-файл + рестарт моста). Понижение ГЛОБАЛЬНО для процесса моста → несовместимо с параллельными
ходами (их FSM ~30c > 8c дали бы ложный 504), поэтому тест идёт только в одиночном прогоне (`-n0`).
Восстановление исходного таймаута — в ``finally`` фикстуры (робастно, всегда). Стабы засижены → реальный ход
доезжает чисто в фоне (поздний push = no-op), teardown idle без зависа.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

import pytest

from threlium.types import IsomorphApiSurface

from .toolkit import E2EComposeRuntime
from .toolkit.isomorph_cline import (
    bridge_post_json,
    clean_isomorph_test_threads,
    e2e_explicit_root_mid,
    e2e_root_prompt_token,
    sut_exec,
    wait_bridge_health,
)
from .toolkit.workers import wait_for_sut_threlium_user_workers_idle
from .wiremock_client import (
    composite_context_key,
    upsert_wiremock_mapping_directory,
    wiremock_public_base,
    wiremock_state_seed_context,
)

_ISO_PORT = 8040
_API_KEY = "e2e-isomorph-api-key"
_MODEL = "claude-sonnet-4-6"
_MARKER = "isomorph-timeout-e2e"
_STUB_TAG = "stub-isomorph-openai-json-e2e-01"  # зашитый tag json-стабов
_STUB_DIR = Path(__file__).parent / "wiremock_stubs" / "test_isomorph_bridge_openai_json_e2e"
_ENV_FILE = "/home/threlium/threlium/agent/env/threlium.env"
_VAR = "THRELIUM_BRIDGES__ISOMORPH__REQUEST_TIMEOUT_SEC"
_LOW_TIMEOUT = 8


def _set_bridge_timeout(rt: E2EComposeRuntime, value: str | int) -> None:
    """Выставить ``request_timeout_sec`` моста (env-файл) + рестарт моста + дождаться health."""
    sut_exec(rt, f"sed -i 's/^{_VAR}=.*/{_VAR}={value}/' {_ENV_FILE}")
    sut_exec(
        rt,
        "export XDG_RUNTIME_DIR=/run/user/$(id -u); "
        "systemctl --user restart threlium-bridge@isomorph.service",
        timeout=40.0,
    )
    wait_bridge_health(rt, port=_ISO_PORT)


@pytest.fixture()
def isomorph_low_timeout(e2e_runtime: E2EComposeRuntime) -> Generator[E2EComposeRuntime, None, None]:
    if os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.skip("lowers bridge request_timeout_sec globally → serial only (-n0)")
    rt = e2e_runtime
    orig = (sut_exec(rt, f"grep -oE '^{_VAR}=[0-9]+' {_ENV_FILE} | cut -d= -f2").strip() or "180")
    wm_base = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
    wait_for_sut_threlium_user_workers_idle(rt.project_name, timeout=30.0)
    clean_isomorph_test_threads(rt, _MARKER)
    upsert_wiremock_mapping_directory(wm_base, _STUB_DIR, stub_tag=_STUB_TAG)
    wiremock_state_seed_context(wm_base, composite_context_key(_STUB_TAG, e2e_explicit_root_mid(_MARKER)))
    _set_bridge_timeout(rt, _LOW_TIMEOUT)
    try:
        yield rt
    finally:
        _set_bridge_timeout(rt, orig)  # восстановить дефолт (робастно — finally всегда)
        wait_for_sut_threlium_user_workers_idle(rt.project_name, timeout=60.0)


def test_isomorph_bridge_upstream_timeout_504(isomorph_low_timeout: E2EComposeRuntime) -> None:
    """При ``request_timeout_sec=8`` push не успевает (FSM ~30c) → мост снимает pending и отдаёт 504 upstream
    timeout (``_await_json``). curl --max-time 40 > 8 → ловим именно мостовой 504, не клиентский обрыв."""
    rt = isomorph_low_timeout
    user = f"ping {e2e_root_prompt_token(_MARKER)} [{_MARKER}]"
    body: dict[str, object] = {
        "model": _MODEL, "stream": False,
        "messages": [
            {"role": "system", "content": "you are an e2e probe"},
            {"role": "user", "content": user},
        ],
    }
    status, resp = bridge_post_json(
        rt, port=_ISO_PORT, path="/v1/chat/completions", body=body,
        api_key=_API_KEY, surface=IsomorphApiSurface.OPENAI_CHAT_COMPLETIONS, timeout=40.0,
    )
    assert status == 504, resp
    assert "timeout" in resp.lower(), resp
