"""``SseFrame`` — единственное место, где живёт грамматика Server-Sent Events (transport).

Энкодеры и keep-alive отдают типизированные ``SseFrame`` VO; сырая SSE-строка
(``event:``/``data:``/``: comment``/``\\n\\n``) появляется **только** в ``SseFrame.render()`` на
крае ``StreamingResponse`` (см. ``server._event_stream``). Полезная нагрузка кадра (JSON события)
уже сериализована типизированными моделями (litellm / anthropic_wire VO) — ``data`` хранит её строку.
"""
from __future__ import annotations

from typing import Self

import msgspec


class SseFrame(msgspec.Struct, frozen=True):
    """Один кадр SSE. Ровно одна из форм: data-only / event+data / comment."""

    data: str | None = None
    event: str | None = None
    comment: str | None = None

    @classmethod
    def of_data(cls, data: str) -> Self:
        """``data: <data>\\n\\n`` (OpenAI chat.completion.chunk, ``[DONE]``, error)."""
        return cls(data=data)

    @classmethod
    def of_event(cls, event: str, data: str) -> Self:
        """``event: <event>\\ndata: <data>\\n\\n`` (Anthropic Messages)."""
        return cls(event=event, data=data)

    @classmethod
    def of_comment(cls, comment: str) -> Self:
        """``: <comment>\\n\\n`` (SSE-комментарий, напр. OpenAI keep-alive)."""
        return cls(comment=comment)

    @classmethod
    def done(cls) -> Self:
        """OpenAI-терминатор ``data: [DONE]\\n\\n``."""
        return cls(data="[DONE]")

    def render(self) -> str:
        lines: list[str] = []
        if self.comment is not None:
            lines.append(f": {self.comment}")
        if self.event is not None:
            lines.append(f"event: {self.event}")
        if self.data is not None:
            lines.append(f"data: {self.data}")
        return "\n".join(lines) + "\n\n"
