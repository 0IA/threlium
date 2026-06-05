"""Нейтральный push-контракт ``egress_isomorph`` → мост (``POST /internal/v1/push``).

Вендор-специфика — только в энкодерах на HTTP-границе; здесь — нейтральные поля, которые
энкодеры мапят в OpenAI (``prompt_tokens``) / Anthropic (``input_tokens``).
"""
from __future__ import annotations

import msgspec

from threlium.types import NonEmptyStr


class IsomorphPushUsage(msgspec.Struct, frozen=True):
    """Нейтральный учёт токенов (энкодеры мапят в вендорные имена)."""

    prompt: int = 0
    completion: int = 0
    total: int = 0


class IsomorphPushToolBlock(msgspec.Struct, frozen=True):
    """Один tool-вызов; ``arguments`` — полная JSON-строка (фаза A: одним фрагментом)."""

    id: str
    name: NonEmptyStr
    arguments: str


class IsomorphBridgePushPayload(msgspec.Struct, frozen=True):
    """Тело ``POST /internal/v1/push``: результат FSM-хода для отдачи клиенту."""

    request_id: NonEmptyStr
    api_surface: NonEmptyStr
    finish_reason: NonEmptyStr
    #: Эхо ``model`` (Cline берёт по нему лимит контекстного окна).
    model: str = ""
    text: str = ""
    tool_blocks: tuple[IsomorphPushToolBlock, ...] = ()
    usage: IsomorphPushUsage = msgspec.field(default_factory=IsomorphPushUsage)
    #: Непустое → mid-stream/terminal error-envelope нужного вендора.
    error_message: str = ""
