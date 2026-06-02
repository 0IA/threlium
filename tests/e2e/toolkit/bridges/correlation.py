"""Shared LiteLLM / WireMock thread-root correlation across transports."""
from __future__ import annotations

from typing import Literal

from .email import e2e_thread_root_mid_for_message_id
from .matrix import e2e_matrix_thread_root_mid_for_sync_event
from .telegram import e2e_telegram_thread_root_mid_for_message


def thread_root_mid_for_native(
    *,
    transport: Literal["email", "matrix", "telegram"],
    **ids: object,
) -> str:
    """Dispatch to transport-specific ``X-Threlium-Thread-Root`` / State key helpers."""
    if transport == "email":
        return e2e_thread_root_mid_for_message_id(str(ids["raw_message_id"]))
    if transport == "matrix":
        return e2e_matrix_thread_root_mid_for_sync_event(
            room_id=str(ids["room_id"]),
            event_id=str(ids["event_id"]),
        )
    if transport == "telegram":
        return e2e_telegram_thread_root_mid_for_message(
            chat_id=int(ids["chat_id"]),  # type: ignore[arg-type]
            message_id=int(ids["message_id"]),  # type: ignore[arg-type]
            message_thread_id=ids.get("message_thread_id"),  # type: ignore[arg-type]
        )
    raise ValueError(f"unsupported transport: {transport!r}")
