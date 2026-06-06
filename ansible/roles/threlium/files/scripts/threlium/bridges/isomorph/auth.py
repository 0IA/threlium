"""Аутентификация клиента: один ``api_key`` через ``x-api-key`` (Anthropic) или ``Authorization: Bearer`` (OpenAI)."""
from __future__ import annotations

import hmac


def extract_client_key(headers: dict[str, str]) -> str:
    """Из заголовков (lower-case ключи) → предъявленный ключ или ``""``."""
    xk = headers.get("x-api-key", "").strip()
    if xk:
        return xk
    auth = headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[len("bearer "):].strip()
    return auth


def is_authorized(headers: dict[str, str], *, api_key: str) -> bool:
    """``True`` если предъявленный ключ совпадает с настроенным (непустым)."""
    expected = (api_key or "").strip()
    if not expected:
        # Без настроенного ключа мост не должен был стартовать (bridge_readiness); fail-closed.
        return False
    # Constant-time: не утекать длину/префикс настроенного ключа по времени ответа.
    # Кодируем в bytes — иначе compare_digest на не-ASCII заголовке бросит TypeError (→ 500 вместо 403).
    return hmac.compare_digest(extract_client_key(headers).encode("utf-8"), expected.encode("utf-8"))
