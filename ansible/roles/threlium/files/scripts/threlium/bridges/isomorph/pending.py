"""``IsomorphPendingRegistry`` — long-hold ожидание push по ``request_id``.

``request_id → (Future, api_surface, stream)``. Резолв из ``/internal/v1/push``; снятие при
disconnect клиента / timeout. Идемпотентность push — на неизвестный/завершённый/снятый id no-op.
"""
from __future__ import annotations

import asyncio

from .push_types import IsomorphBridgePushPayload


class _Pending:
    __slots__ = ("future", "api_surface", "stream")

    def __init__(self, future: "asyncio.Future[IsomorphBridgePushPayload]", api_surface: str, stream: bool) -> None:
        self.future = future
        self.api_surface = api_surface
        self.stream = stream


class IsomorphPendingRegistry:
    def __init__(self) -> None:
        self._by_id: dict[str, _Pending] = {}

    def register(
        self, request_id: str, *, api_surface: str, stream: bool
    ) -> "asyncio.Future[IsomorphBridgePushPayload]":
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[IsomorphBridgePushPayload]" = loop.create_future()
        self._by_id[request_id] = _Pending(fut, api_surface, stream)
        return fut

    def resolve(self, payload: IsomorphBridgePushPayload) -> bool:
        """Разрешить pending. ``True`` если был активный; иначе (unknown/done/cancelled) ``False`` (no-op)."""
        entry = self._by_id.get(payload.request_id)
        if entry is None or entry.future.done():
            return False
        entry.future.set_result(payload)
        return True

    def discard(self, request_id: str) -> None:
        """Снять pending (disconnect/timeout); поздний push станет no-op."""
        entry = self._by_id.pop(request_id, None)
        if entry is not None and not entry.future.done():
            entry.future.cancel()

    def forget(self, request_id: str) -> None:
        """Убрать запись без отмены (после успешной отдачи ответа)."""
        self._by_id.pop(request_id, None)
