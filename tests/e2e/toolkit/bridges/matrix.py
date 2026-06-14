"""Matrix e2e correlation IDs."""
from __future__ import annotations

import uuid

from threlium.types import (
    MatrixNativeId,
    MatrixRoomEventId,
    MatrixRoomId,
    NotmuchMessageIdInner,
    RfcMessageIdWire,
)
from threlium.types.litellm_correlation_header import thread_root_hash

def e2e_matrix_thread_root_mid_for_sync_event(*, room_id: str, event_id: str) -> str:
    """Коррелятор ``X-Threlium-Thread-Root`` matrix-треда: ``sha256`` от inner-``Message-ID`` корня (room+event).

    Корень — ``RfcMessageIdWire.from_native(MatrixNativeId(v=1, …))`` как в :mod:`threlium.bridges.matrix`;
    продукт кладёт в заголовок ``thread_root_hash`` от этого же inner-``Message-ID`` (LiteLLM / WireMock
    State ``correlation_key``), поэтому тест считает тот же хэш.
    """
    inner = _matrix_message_id_inner(room_id=room_id, event_id=event_id)
    return thread_root_hash(inner.as_angle_bracket_header())


def _matrix_message_id_inner(*, room_id: str, event_id: str) -> NotmuchMessageIdInner:
    native = MatrixNativeId(
        v=1,
        room_id=MatrixRoomId(room_id.strip()),
        event_id=MatrixRoomEventId(event_id.strip()),
    )
    return NotmuchMessageIdInner.from_present_wire(RfcMessageIdWire.from_native(native))


def e2e_matrix_nm_inner_for_sync_event(*, room_id: str, event_id: str) -> str:
    """UN-hashed inner ``Message-ID`` (``notmuch id:``-форма) корня matrix-треда.

    Отличается от :func:`e2e_matrix_thread_root_mid_for_sync_event` (возвращает ``thread_root_hash`` =
    sha256 = ``X-Threlium-Thread-Root`` / WireMock ``correlation_key`` для гейтинга стабов). Здесь — то,
    по чему ``notmuch search id:`` находит входящее письмо после моста. Нужен для регистрации drain-треда
    (:func:`e2e_record_test_drain_thread`): по хэшу ``notmuch id:`` не резолвится → global fallback. См.
    [[n12-stub-churn-404-worker-crash-root]] + docs/E2E.md (B)/(P) stub-lifecycle.
    """
    return _matrix_message_id_inner(room_id=room_id, event_id=event_id).value


def e2e_matrix_generate_room_ids() -> tuple[str, str]:
    """Сгенерировать уникальную пару ``(room_id, event_id)`` для Matrix e2e теста.

    ``room_id`` — ``!e2e_<hex>:mock``, ``event_id`` — ``$evt_<hex>``.
    Используется для ``register_room`` в WireMock State и вычисления ``correlation_key``.
    """
    room_id = f"!e2e_{uuid.uuid4().hex[:16]}:mock"
    event_id = f"$evt_{uuid.uuid4().hex[:20]}"
    return room_id, event_id
