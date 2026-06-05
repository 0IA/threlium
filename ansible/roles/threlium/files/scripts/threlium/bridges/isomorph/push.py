"""Логика ``POST /internal/v1/push``: localhost + ``push_secret`` + идемпотентный резолв pending."""
from __future__ import annotations

import hmac

import msgspec

from .pending import IsomorphPendingRegistry
from .push_types import IsomorphBridgePushPayload

_PUSH_SECRET_HEADER = "x-threlium-push-secret"
_LOCALHOST = frozenset({"127.0.0.1", "::1", "localhost"})


class PushResult(msgspec.Struct, frozen=True):
    status: int
    detail: str


def handle_push(
    body: bytes,
    headers: dict[str, str],
    *,
    client_host: str | None,
    registry: IsomorphPendingRegistry,
    push_secret: str,
) -> PushResult:
    """Проверки + резолв. Идемпотентно: unknown/done/cancelled → 204 (не ошибка)."""
    if client_host is not None and client_host not in _LOCALHOST:
        return PushResult(status=403, detail="push: localhost only")

    presented = headers.get(_PUSH_SECRET_HEADER, "")
    if not push_secret or not hmac.compare_digest(presented, push_secret):
        return PushResult(status=403, detail="push: bad secret")

    try:
        payload = msgspec.json.decode(body, type=IsomorphBridgePushPayload)
    except (msgspec.DecodeError, msgspec.ValidationError) as e:
        return PushResult(status=400, detail=f"push: bad payload: {e}")

    delivered = registry.resolve(payload)
    # Идемпотентность: повторный/поздний push на снятый pending — 204, не ошибка.
    return PushResult(status=200 if delivered else 204, detail="ok" if delivered else "no-op")
