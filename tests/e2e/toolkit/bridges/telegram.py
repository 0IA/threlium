"""Telegram e2e correlation IDs."""
from __future__ import annotations

import uuid

from threlium.types import (
    NotmuchMessageIdInner,
    RfcMessageIdWire,
    TelegramNativeId,
)

def e2e_telegram_generate_update_bundle(
    *,
    with_forum_topic: bool,
) -> tuple[int, int, int, int | None]:
    """Уникальные ``(chat_id, message_id, update_id, message_thread_id)`` для Telegram e2e.

    ``message_thread_id`` — ``None`` в личке; в forum topic — положительное int, отличное от
    ``message_id``, чтобы не путать смыслы полей.
    """
    chat_id = int(uuid.uuid4().int % 900_000_000) + 100_000_000
    message_id = int(uuid.uuid4().int % 90_000) + 10_000
    update_id = int(uuid.uuid4().int % 900_000_000) + 100_000_000
    mtid: int | None
    if with_forum_topic:
        mtid = int(uuid.uuid4().int % 90_000) + 50_000
        while mtid == message_id:
            mtid = int(uuid.uuid4().int % 90_000) + 50_000
    else:
        mtid = None
    return chat_id, message_id, update_id, mtid


def e2e_telegram_thread_root_mid_for_message(
    *,
    chat_id: int,
    message_id: int,
    message_thread_id: int | None,
) -> str:
    """Уголковый ``Message-ID`` корня треда (как ``X-Threlium-Thread-Root`` / WireMock ``correlation_key``).

    Совпадает с :mod:`threlium.bridges.telegram` (``RfcMessageIdWire.from_native(TelegramNativeId(…))``).
    """
    native = TelegramNativeId(
        v=1,
        chat_id=chat_id,
        message_id=message_id,
        message_thread_id=message_thread_id,
    )
    mid_wire = RfcMessageIdWire.from_native(native)
    inner = NotmuchMessageIdInner.from_present_wire(mid_wire)
    return inner.as_angle_bracket_header()
