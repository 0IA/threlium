"""Аутентификация клиента: один ``api_key`` через ``x-api-key`` (Anthropic) или ``Authorization: Bearer`` (OpenAI)."""
from __future__ import annotations


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
    return extract_client_key(headers) == expected
