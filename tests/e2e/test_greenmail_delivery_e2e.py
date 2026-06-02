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

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path

from .mail_wire import e2e_smtp_send
from .helpers import (
    E2EComposeRuntime,
    TIMEOUT_POLL_SHORT,
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


@contextmanager
def greenmail_wiremock_scope(
    e2e_runtime: E2EComposeRuntime,
) -> Generator[tuple[str, str], None, None]:
    """Стабы на общем WireMock + State (контекст = b62 ``X-Threlium-Route`` для ``Message-ID``)."""
    wm = wiremock_public_base(e2e_runtime.wiremock_host, e2e_runtime.wiremock_port)
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


def test_greenmail_inbox_delivery_smoke(e2e_runtime: E2EComposeRuntime) -> None:
    with greenmail_wiremock_scope(e2e_runtime) as (correlation_key, msg_id):
        wait_for_greenmail_ready(e2e_runtime.project_name, timeout=TIMEOUT_POLL_SHORT)
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

        e2e_smtp_send(
            e2e_runtime.greenmail_smtp_host,
            e2e_runtime.greenmail_smtp_port,
            msg,
            timeout=float(TIMEOUT_POLL_SHORT),
        )

        wait_for_greenmail_inbox_message_host(
            e2e_runtime.greenmail_imap_host,
            e2e_runtime.greenmail_imap_port,
            message_id=msg_id,
            timeout=TIMEOUT_POLL_SHORT,
        )
