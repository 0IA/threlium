"""Parse tool_calls → :class:`ConfirmCliHitlToolArgs` для ``cli_resume``.

Сырой JSON args — :class:`~threlium.types.litellm_tool_call.LiteLlmToolCallArgumentsWire`
(``from_tool_call`` / ``validate_tool_args_json``), по контракту ``docs/TYPES.md``.
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
from threlium.types.cli_hitl_tool_args import ConfirmCliHitlToolArgs
from threlium.types.cli_hitl_tool_function import (
    CliHitlBridgeError,
    CliHitlToolFunctionName,
)
from threlium.types.litellm_tool_call import LiteLlmToolCallArgumentsWire

_CONTEXT = "cli_hitl_resume"


def parse_confirm_cli_hitl_from_wire(
    wire: LiteLlmToolCallArgumentsWire,
) -> ConfirmCliHitlToolArgs:
    """jsonschema + msgspec по wire args tool ``confirm_cli_hitl``."""
    spec = load_tool_spec(PromptPath.CLI_RESUME_CONFIRM_CLI_HITL_TOOL_SPEC)
    schema = tool_spec_parameters(spec)
    try:
        args_dict = validate_tool_args_json(schema, wire)
    except jsonschema.ValidationError as exc:
        raise CliHitlBridgeError(
            f"{_CONTEXT}: confirm_cli_hitl arguments failed jsonschema"
        ) from exc
    try:
        return msgspec.convert(args_dict, type=ConfirmCliHitlToolArgs)
    except (RuntimeError, ValueError, msgspec.ValidationError) as exc:
        raise CliHitlBridgeError(
            f"{_CONTEXT}: invalid confirm_cli_hitl arguments"
        ) from exc


def parse_confirm_cli_hitl_assistant(assistant: Message) -> ConfirmCliHitlToolArgs:
    """Распарсить assistant message после ``require_tool_calls_response``."""
    tc = require_single_tool_call(assistant, context=_CONTEXT)
    name = CliHitlToolFunctionName.parse_tool_call(tc)
    name.assert_matches(CliHitlToolFunctionName.CONFIRM_CLI_HITL)
    wire = LiteLlmToolCallArgumentsWire.from_tool_call(tc)
    return parse_confirm_cli_hitl_from_wire(wire)


def parse_confirm_cli_hitl(msg: Message) -> ConfirmCliHitlToolArgs:
    """Alias: полный parse от assistant message (включая require_single_tool_call)."""
    return parse_confirm_cli_hitl_assistant(msg)


__all__ = [
    "parse_confirm_cli_hitl",
    "parse_confirm_cli_hitl_assistant",
    "parse_confirm_cli_hitl_from_wire",
]
