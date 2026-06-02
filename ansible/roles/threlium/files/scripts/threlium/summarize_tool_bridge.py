"""Parse tool_calls → args суммаризации (thread context / response buffer).

Контракт ``docs/TYPES.md`` § tool bridge.
"""
from __future__ import annotations

import jsonschema
import msgspec
from litellm.types.utils import Message

from threlium.litellm_tool_response import require_single_tool_call
from threlium.litellm_tool_spec import (
    load_tool_spec,
    tool_spec_parameters,
    validate_tool_args_json,
)
from threlium.types import PromptPath
from threlium.types.litellm_tool_call import LiteLlmToolCallArgumentsWire
from threlium.types.summarize_tool_args import (
    SummarizeResponseBufferToolArgs,
    SummarizeThreadContextToolArgs,
)
from threlium.types.summarize_tool_function import (
    SummarizeToolBridgeError,
    SummarizeToolFunctionName,
)

_THREAD_CONTEXT = "summarize_thread_context"
_BUFFER_CONTEXT = "summarize_response_buffer"


def parse_summarize_thread_context_assistant(
    assistant: Message,
) -> SummarizeThreadContextToolArgs:
    tc = require_single_tool_call(assistant, context=_THREAD_CONTEXT)
    name = SummarizeToolFunctionName.parse_tool_call(tc)
    name.assert_matches(SummarizeToolFunctionName.SUMMARIZE_THREAD_CONTEXT)
    spec = load_tool_spec(PromptPath.SUMMARIZE_CONTEXT_TOOL_SPEC)
    schema = tool_spec_parameters(spec)
    wire = LiteLlmToolCallArgumentsWire.from_tool_call(tc)
    try:
        args_dict = validate_tool_args_json(schema, wire)
    except jsonschema.ValidationError as exc:
        raise SummarizeToolBridgeError(
            f"{_THREAD_CONTEXT}: arguments failed jsonschema"
        ) from exc
    try:
        return msgspec.convert(args_dict, type=SummarizeThreadContextToolArgs)
    except (RuntimeError, ValueError, msgspec.ValidationError) as exc:
        raise SummarizeToolBridgeError(
            f"{_THREAD_CONTEXT}: invalid arguments"
        ) from exc


def parse_summarize_response_buffer_assistant(
    assistant: Message,
) -> SummarizeResponseBufferToolArgs:
    tc = require_single_tool_call(assistant, context=_BUFFER_CONTEXT)
    name = SummarizeToolFunctionName.parse_tool_call(tc)
    name.assert_matches(SummarizeToolFunctionName.SUMMARIZE_RESPONSE_BUFFER)
    spec = load_tool_spec(PromptPath.RESPONSE_OBSERVE_TOOL_SPEC)
    schema = tool_spec_parameters(spec)
    wire = LiteLlmToolCallArgumentsWire.from_tool_call(tc)
    try:
        args_dict = validate_tool_args_json(schema, wire)
    except jsonschema.ValidationError as exc:
        raise SummarizeToolBridgeError(
            f"{_BUFFER_CONTEXT}: arguments failed jsonschema"
        ) from exc
    try:
        return msgspec.convert(args_dict, type=SummarizeResponseBufferToolArgs)
    except (RuntimeError, ValueError, msgspec.ValidationError) as exc:
        raise SummarizeToolBridgeError(
            f"{_BUFFER_CONTEXT}: invalid arguments"
        ) from exc


__all__ = [
    "parse_summarize_response_buffer_assistant",
    "parse_summarize_thread_context_assistant",
]
