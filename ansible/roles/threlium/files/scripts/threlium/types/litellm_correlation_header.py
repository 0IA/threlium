"""HTTP-заголовки e2e-корреляции LiteLLM (не RFC822 заголовки писем).

Живут в ``extra_headers`` dict HTTP-запросов к LLM API / WireMock.
Не записываются в ``EmailMessage``, не индексируются в notmuch.
"""
from __future__ import annotations

import hashlib
from enum import StrEnum


class LitellmCorrelationHeader(StrEnum):
    """Wire-имена HTTP-заголовков корреляции (``extra_headers`` в вызовах LiteLLM)."""

    CALL_SITE = "X-Threlium-Call-Site"
    LITELLM_REQUEST_SEQ = "X-Threlium-Litellm-Req-Seq"
    THREAD_ROOT_MID = "X-Threlium-Thread-Root"


def thread_root_hash(angle_bracket_mid: str) -> str:
    """Стабильный короткий коррелятор треда для ``X-Threlium-Thread-Root``: sha256-hex от inner Message-ID корня треда.

    Единственная точка генерации значения ``X-Threlium-Thread-Root`` (продукт и тесты считают
    один и тот же хэш от одного и того же inner-``Message-ID``). Заменяет длинный
    ``b62(JSON(mid))`` wire-``Message-ID`` (~110 симв.), у которого уникальная энтропия — в хвосте
    за общим ~46-символьным префиксом: усечение длинных значений заголовков (notmuch / WireMock
    State key / прочие места) схлопывало разные треды в один корень → коллизия → ``nm LookupError``
    → ретрай-шторм FSM. sha256 — фиксированная длина с энтропией по всем символам → усечение не
    даёт коллизий. hex — валидные символы Message-ID, ``base62`` поверх не нужен. Скобки ``<>``
    нормализуются, чтобы обе стороны хэшировали идентичный inner. НЕ трогает внутренний кодек
    ``RfcMessageIdWire`` / notmuch-id / ``X-Threlium-Route`` — только тестовый коррелятор треда.
    """
    inner = angle_bracket_mid.strip().strip("<>").strip()
    return hashlib.sha256(inner.encode("utf-8")).hexdigest()
