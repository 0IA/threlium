"""Wire-типы для аргументов OpenAI tool_calls (reasoning, LightRAG, …)."""
from __future__ import annotations

from typing import Self

from threlium.llm_wire import LlmToolCall as ChatCompletionMessageToolCall

from threlium.types._core import _OptionalStripEmpty


class LiteLlmToolCallArgumentsWire(_OptionalStripEmpty):
    """Сырой JSON аргументов tool_call (wire до jsonschema / msgspec)."""

    @classmethod
    def from_tool_call(cls, tc: ChatCompletionMessageToolCall) -> Self:
        func = tc.function
        if func is None:
            raise RuntimeError("tool_call without function")
        raw = func.arguments
        if isinstance(raw, bytes):
            return cls.parse(raw.decode("utf-8", errors="replace"))
        return cls.parse(raw if isinstance(raw, str) else "")


__all__ = ["LiteLlmToolCallArgumentsWire"]
