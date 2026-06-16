"""E2e: happy-path канала ``isomorph`` — **OpenAI surface, НЕ-SSE** (``/v1/chat/completions``, ``stream:false``).

В отличие от cline-тестов (реальный клиент + SSE), здесь тест сам — прямой HTTP-клиент: POST ИЗНУТРИ SUT
на loopback моста. ``stream:false`` → мост держит соединение (long-hold), FSM прогоняет ход
(ingress → … → ``egress_isomorph`` push), мост отдаёт финальный **JSON** (``chat.completion``) — ветка
``_await_json``. SSE-зеркало — [test_isomorph_bridge_openai_cline_e2e.py](test_isomorph_bridge_openai_cline_e2e.py).

Тест владеет телом запроса целиком → thread-root **предвычисляется** из ЭТОГО тела тем же кодом моста
(:func:`thread_root_from_body`), без Cline / даты / шаблона системного промпта. State-контекст WireMock
сидится **до** POST (сид данных, НЕ генерация стабов) — гонки нет. Изоляция: своя папка стабов, свой
``stub_tag``; setup чистит ТОЛЬКО прошлые треды этого теста (по маркеру), teardown ничего не стирает.
"""
from __future__ import annotations

import uuid

import json
from pathlib import Path
from typing import Generator

import pytest

from threlium.types import IsomorphApiSurface

from .toolkit import E2EComposeRuntime, poll_until
from .toolkit.constants import TIMEOUT_POLL_LIVE_MAIL
from .toolkit.isomorph_cline import (
    bridge_post_json,
    build_continuation_body,
    extract_reply_text,
    nm_count,
    nm_count_in_test_thread,
    nm_oldest_message_id,
    nm_test_thread_count,
    thread_root_from_body,
    wait_bridge_health,
)
from .toolkit.workers import wait_for_sut_threlium_user_workers_idle
from .wiremock_client import (
    upsert_wiremock_mapping_directory,
    wiremock_public_base,
    wiremock_seed_reasoning_phases,
    wiremock_state_seed_context,
)
from threlium.types.litellm_correlation_header import thread_root_hash

_ISO_PORT = 8040
_API_KEY = "e2e-isomorph-api-key"
_MODEL = "claude-sonnet-4-6"
_SURFACE = IsomorphApiSurface.OPENAI_CHAT_COMPLETIONS
_PATH = "/v1/chat/completions"
_STUB_TAG = "stub-isomorph-openai-json-e2e-01"
_STUB_DIR = Path(__file__).parent / "wiremock_stubs" / "test_isomorph_bridge_openai_json_e2e"
_MARKER = f"isomorph-openai-json-e2e-{uuid.uuid4().hex[:12]}"
#: ОТДЕЛЬНЫЙ маркер multiturn-теста (свой uuid): happy_path и multiturn иначе делят content-addressed
#: thread-root (тот же _BODY) → одинаковый Message-ID → коллизия bridge-pending. Свой маркер = свой тред.
_MARKER_MT = f"isomorph-openai-json-mt-{uuid.uuid4().hex[:12]}"
_REPLY_MARKER = "ok from llm-mock"
#: Тело запроса целиком во власти теста (system+user сольются в один хвост → детерминированный thread-root).
_BODY: dict[str, object] = {
    "model": _MODEL,
    "stream": False,
    "messages": [
        {"role": "system", "content": "you are an e2e probe"},
        {"role": "user", "content": f"ping [{_MARKER}]"},
    ],
}


def _thread_root() -> str:
    return thread_root_from_body(_SURFACE, _BODY)


# Detag (§3.6.8): generic reasoning, 2 линейные фазы (tasks_upsert → finalize) на один ход. isomorph —
# не mailflow, поэтому фазы сидим тест-сайдом по ЧИСТОМУ thread-root (= thread_root_hash(тело)); между
# ходами повторный seed сбрасывает phase=0 (019 ставит phase=0+gen_reasoning=1+p_i). content финализа
# несёт _REPLY_MARKER.
_ISO_PHASES = [
    (
        "tasks_upsert",
        {
            "reasoning": "e2e: record task completion before finalize",
            "new_subtasks": [{"text": "Complete the user request", "status": "done"}],
        },
    ),
    (
        "response_finalize",
        {
            "reasoning": "e2e: finalizing response with verified content",
            "subject": "e2e reply",
            "verification_summary": "e2e: direct answer, content verified",
            "content": _REPLY_MARKER,
        },
    ),
]


@pytest.fixture()
def isomorph_json(e2e_runtime: E2EComposeRuntime) -> Generator[E2EComposeRuntime, None, None]:
    """Setup ДО guard'а unmatched: settle, scoped-чистка СВОИХ прошлых тредов, стабы, СИД thread-root.

    Сид предвычисленного thread-root **до** POST → первый LLM-вызов уже сматчен, гонки нет. Teardown НЕ
    стирает данные (остаются для отладки); свои прошлые данные затрёт setup следующего прогона.
    """
    rt = e2e_runtime
    wm_base = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
    wait_for_sut_threlium_user_workers_idle(rt.project_name, timeout=30.0)
    wait_bridge_health(rt, port=_ISO_PORT)  # мост мог ещё не подняться после сессионного cold-reset
    upsert_wiremock_mapping_directory(wm_base, _STUB_DIR, stub_tag=_STUB_TAG)
    _tr = thread_root_hash(_thread_root())
    wiremock_state_seed_context(wm_base, _tr)
    wiremock_seed_reasoning_phases(wm_base, _tr, _ISO_PHASES)
    try:
        yield rt
    finally:
        wait_for_sut_threlium_user_workers_idle(rt.project_name, timeout=60.0)


def test_isomorph_bridge_openai_json_happy_path(isomorph_json: E2EComposeRuntime) -> None:
    """POST /v1/chat/completions stream:false → bridge → FSM → egress push → финальный JSON chat.completion."""
    rt = isomorph_json
    status, resp = bridge_post_json(
        rt, port=_ISO_PORT, path=_PATH, body=_BODY, api_key=_API_KEY, surface=_SURFACE
    )
    assert status == 200, resp
    payload = json.loads(resp)
    # OpenAI JSON-форма (encode_openai_json): object=chat.completion + choices[].message.content.
    assert payload.get("object") == "chat.completion", payload
    content = "".join(
        (c.get("message", {}) or {}).get("content", "") or ""
        for c in payload.get("choices", []) if isinstance(c, dict)
    )
    assert _REPLY_MARKER in content, payload

    # Скоуп по _MARKER (teardown не стирает → глобальный from:egress поймал бы чужой glue).
    assert nm_oldest_message_id(rt, f"from:isomorph@localhost and {_MARKER}") == _thread_root().strip("<>")
    assert nm_count(rt, f"from:isomorph@localhost and {_MARKER}") >= 1, "no isomorph ingress in notmuch"
    assert nm_count_in_test_thread(rt, _MARKER, "from:egress_isomorph@localhost") >= 1, "no egress glue"


def test_isomorph_bridge_openai_json_multiturn_continuity(isomorph_json: E2EComposeRuntime) -> None:
    """Непрерывность ЧЕРЕЗ ВОДЯНОЙ ЗНАК (OpenAI surface): ход-2 несёт ответ хода-1 как last-assistant; мост
    декодит из него невидимый glue-snowflake → ``In-Reply-To`` = glue хода-1 → ОДИН тред. БЕЗ
    notmuch-голосования и БЕЗ in-work-409. Между ходами — только phase_reset (reasoning-защёлка хода-1)."""
    rt = isomorph_json
    wm = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
    # Своё тело хода-1 (свой _MARKER_MT) → свой content-addressed thread-root, НЕ коллизирующий с happy_path.
    # Сидим свой контекст (фикстура засидила happy_path-root, нам нужен свой); стабы templated по thread-root.
    body1: dict[str, object] = {**_BODY, "messages": [{"role": "user", "content": f"ping mt [{_MARKER_MT}]"}]}
    ctx1 = thread_root_hash(thread_root_from_body(_SURFACE, body1))
    wiremock_state_seed_context(wm, ctx1)
    wiremock_seed_reasoning_phases(wm, ctx1, _ISO_PHASES)
    s1, r1 = bridge_post_json(rt, port=_ISO_PORT, path=_PATH, body=body1, api_key=_API_KEY, surface=_SURFACE)
    assert s1 == 200, r1
    reply1 = extract_reply_text(_SURFACE, r1)
    assert _REPLY_MARKER in reply1, r1

    # Сброс фаз generic-reasoning между ходами (свой pure-контекст треда): повторный seed ставит phase=0
    # (+gen_reasoning=1+p_i), иначе ход-2 продолжил бы счётчик хода-1 и упёрся в пустую фазу.
    wiremock_seed_reasoning_phases(wm, ctx1, _ISO_PHASES)
    # Ход-2: reply1 несёт невидимый водяной знак glue хода-1 → мост декодит → IRT (без notmuch). Пост идёт
    # напрямую — без ожидания индексации glue и без 409-ретрая (механизмы сняты вместе с voting).
    body2 = build_continuation_body(_SURFACE, body1, reply1, f"continue [{_MARKER_MT}]")
    s2, r2 = bridge_post_json(rt, port=_ISO_PORT, path=_PATH, body=body2, api_key=_API_KEY, surface=_SURFACE)
    assert s2 == 200, r2
    assert _REPLY_MARKER in extract_reply_text(_SURFACE, r2), r2

    # Дождаться индексации обоих ингрессов + glue перед notmuch-проверками (фоновый settle).
    poll_until(
        lambda: True if (
            nm_count(rt, f"from:isomorph@localhost and {_MARKER_MT}") == 2
            and nm_count_in_test_thread(rt, _MARKER_MT, "from:egress_isomorph@localhost") >= 1
        ) else None,
        timeout=TIMEOUT_POLL_LIVE_MAIL, desc="both turns + glue indexed",
    )
    # Непрерывность: оба хода в ОДНОМ треде (иначе ход-2 ушёл бы в orphan → 2 треда), 2 разных ingress.
    assert nm_test_thread_count(rt, _MARKER_MT) == 1, "turn-2 orphaned → watermark continuity broke"
    assert nm_count(rt, f"from:isomorph@localhost and {_MARKER_MT}") == 2, "expected 2 distinct ingress turns"
    assert nm_count_in_test_thread(rt, _MARKER_MT, "from:egress_isomorph@localhost") >= 1, "no egress glue"
