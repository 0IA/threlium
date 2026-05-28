"""HTTP-заголовки e2e-корреляции LiteLLM (не RFC822 заголовки писем).

Живут в ``extra_headers`` dict HTTP-запросов к LLM API / WireMock.
Не записываются в ``EmailMessage``, не индексируются в notmuch.
"""
from __future__ import annotations

from enum import StrEnum


class LitellmCorrelationHeader(StrEnum):
    """Wire-имена HTTP-заголовков корреляции (``extra_headers`` в вызовах LiteLLM)."""

    CALL_SITE = "X-Threlium-Call-Site"
    LITELLM_REQUEST_SEQ = "X-Threlium-Litellm-Req-Seq"
    THREAD_ROOT_MID = "X-Threlium-Thread-Root"
