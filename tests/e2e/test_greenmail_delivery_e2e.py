"""E2e smoke: доставка в GreenMail и чтение по IMAP (уровень 1 из ``docs/TESTING.md``).

**Тест-кейс: SMTP → ящик GreenMail → IMAP на хосте pytest.** Проверяется только почтовый контур до
бриджа/SUT: письмо дошло до тестового сервера и видно по якорям во входящих.

**Цель.** С хоста письмо принимается GreenMail и читается по IMAP с теми же ``Message-ID``/``Subject``.
Модуль **не** поднимает compose, **не** вызывает Ansible и **не** инициирует bake — нужен уже
запущенный e2e-стек. На общем WireMock регистрируются стабы ``test_greenmail_delivery_e2e`` (якорь в теле —
``GREENMAIL_SMOKE_BODY_ANCHOR``), чтобы исходящие HTTP-вызовы конвейера (если письмо заберёт бридж)
не пересекались с чужими ``bodyPatterns``. Индексацию notmuch/Maildir в SUT **вручную не подменяем** —
см. §8 «политика честности» в ``docs/TESTING.md``; полный контур — ``test_mailflow_*``.
"""
from __future__ import annotations

import smtplib
import uuid
from email.message import EmailMessage
from pathlib import Path

import pytest

from .helpers import (
    TIMEOUT_POLL_SHORT,
    discover_live_e2e_project_name,
    discover_runtime,
    e2e_dense_threlium_ctx_body,
    e2e_greenmail_mailbox_address,
    e2e_thread_root_mid_for_message_id,
    wait_for_greenmail_inbox_message_host,
    wait_for_greenmail_ready,
)
from .wiremock_client import (
    prepare_wiremock_scenario,
    teardown_wiremock_scenario,
    wiremock_public_base,
)

_WIREMOCK_STUBS_ROOT = Path(__file__).resolve().parent / "wiremock_stubs"
GREENMAIL_SMOKE_STUB_TAG = "stub-greenmail-delivery-smoke-01"
GREENMAIL_SMOKE_BODY_ANCHOR = "E2E-GM-SMOKE-ANCHOR-01"
GREENMAIL_SMOKE_STUB_DIR = _WIREMOCK_STUBS_ROOT / "test_greenmail_delivery_e2e"


@pytest.fixture(scope="module")
def live_greenmail_runtime():
    pn = discover_live_e2e_project_name()
    if not pn:
        pytest.skip(
            "No live e2e stack: start compose (pytest tests/e2e / wipe_bake)."
        )
    try:
        return discover_runtime(pn)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"live e2e stack not reachable: {e}")


@pytest.fixture
def greenmail_wiremock_stubs(live_greenmail_runtime):
    """Стабы на общем WireMock + State (контекст = тот же b62 ``X-Threlium-Route``, что для ``Message-ID`` письма)."""
    rt = live_greenmail_runtime
    wm = wiremock_public_base(rt.wiremock_host, rt.wiremock_port)
    msg_id = f"e2e-greenmail-{uuid.uuid4().hex}@localhost"
    correlation_key = e2e_thread_root_mid_for_message_id(msg_id)
    prepare_wiremock_scenario(
        wm,
        stub_dir=GREENMAIL_SMOKE_STUB_DIR,
        stub_tag=GREENMAIL_SMOKE_STUB_TAG,
        correlation_key=correlation_key,
    )
    try:
        yield correlation_key, msg_id
    finally:
        teardown_wiremock_scenario(
            wm, correlation_key=correlation_key, stub_tag=GREENMAIL_SMOKE_STUB_TAG
        )


@pytest.mark.e2e
@pytest.mark.e2e_live
def test_greenmail_inbox_delivery_smoke(
    live_greenmail_runtime,
    greenmail_wiremock_stubs: tuple[str, str],
) -> None:
    runtime = live_greenmail_runtime
    correlation_key, msg_id = greenmail_wiremock_stubs
    wait_for_greenmail_ready(runtime.project_name, timeout=TIMEOUT_POLL_SHORT)
    subject = f"e2e greenmail smoke {GREENMAIL_SMOKE_BODY_ANCHOR}"

    msg = EmailMessage()
    msg["From"] = "pytest@localhost"
    # GreenMail: ``-Dgreenmail.users=…@localhost`` — в ``RCPT TO`` нужен полный адрес (``test@localhost``).
    msg["To"] = e2e_greenmail_mailbox_address("test")
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{msg_id}>"
    msg.set_content(
        e2e_dense_threlium_ctx_body(
            head=(
                "greenmail smoke message\n\n"
                f"{GREENMAIL_SMOKE_BODY_ANCHOR}"
            ),
            correlation_key=correlation_key,
        )
    )

    with smtplib.SMTP(
        runtime.greenmail_smtp_host,
        runtime.greenmail_smtp_port,
        timeout=int(TIMEOUT_POLL_SHORT),
    ) as smtp:
        smtp.send_message(msg)

    wait_for_greenmail_inbox_message_host(
        runtime.greenmail_imap_host,
        runtime.greenmail_imap_port,
        message_id=msg_id,
        timeout=TIMEOUT_POLL_SHORT,
    )
