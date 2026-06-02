"""Имена OpenAI tools суммаризации (thread context / response buffer)."""
from __future__ import annotations

from enum import StrEnum
from typing import Self

from litellm.types.utils import ChatCompletionMessageToolCall


class SummarizeToolBridgeError(RuntimeError):
    """Ошибка bridge tool_calls → args для суммаризации."""


class SummarizeToolFunctionName(StrEnum):
    SUMMARIZE_THREAD_CONTEXT = "summarize_thread_context"
    SUMMARIZE_RESPONSE_BUFFER = "summarize_response_buffer"

    @classmethod
    def parse_tool_call(cls, tc: ChatCompletionMessageToolCall) -> Self:
        func = tc.function
        if func is None or not func.name:
            raise SummarizeToolBridgeError("tool_call without function.name")
        raw = func.name.strip()
        try:
            return cls(raw)
        except ValueError as exc:
            raise SummarizeToolBridgeError(
                f"unknown summarize tool function.name={raw!r}"
            ) from exc

    def assert_matches(self, expected: SummarizeToolFunctionName) -> None:
        if self != expected:
            raise SummarizeToolBridgeError(
                f"expected tool {expected.value!r}, got {self.value!r}"
            )


__all__ = ["SummarizeToolBridgeError", "SummarizeToolFunctionName"]
