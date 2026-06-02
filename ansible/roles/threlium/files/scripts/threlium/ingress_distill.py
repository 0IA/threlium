"""Ingress distill: LLM tool_choice=required → structured brief in ``<history>``."""
from __future__ import annotations

import json
from typing import cast

import jsonschema
from email.message import EmailMessage
from threlium.enrich_context import trim_context_text
from threlium.ingress_distill_tool_bridge import parse_ingress_distill_assistant
from threlium.litellm_required_tool import invoke_required_tool
from threlium.litellm_route_context import get_litellm_http_correlation
from threlium.litellm_tool_response import LiteLlmToolResponseError
from threlium.litellm_tool_spec import tool_spec_parameters
from threlium.logutil import logger
from threlium.prompts import render_prompt
from threlium.settings import ThreliumSettings, resolve_llm_endpoint
from threlium.types import (
    IngressDistillBridgeError,
    IngressDistillEnvelope,
    IngressDistillResult,
    IngressExternalBodyText,
    LiteLlmAcompletionKwargs,
    LiteLlmChatMessage,
    LitellmRoutingSite,
    PromptPath,
    ingress_distill_fallback_history_parts,
    ingress_distill_history_parts_from_tool_args,
)
log = logger.bind(stage="ingress")

_MAX_INGRESS_DISTILL_RETRIES = 2


def load_ingress_distill_tool_spec(config: ThreliumSettings) -> dict[str, object]:
    rendered = render_prompt(
        PromptPath.INGRESS_DISTILL_TOOL_SPEC,
        distill_max_chars=config.ingress.distill_max_chars,
    )
    raw = json.loads(rendered)
    if not isinstance(raw, dict):
        raise RuntimeError("ingress_distill tool spec JSON must be an object")
    return cast(dict[str, object], raw)


def ingress_distill_llm(
    envelope: IngressDistillEnvelope,
    msg: EmailMessage,
    *,
    config: ThreliumSettings,
) -> IngressDistillResult:
    """Sync LLM distill with tool bridge; retry; fallback to trimmed full_body."""
    ep = resolve_llm_endpoint(config.litellm, LitellmRoutingSite.INGRESS_DISTILL)
    mr = ep.max_retries if ep.max_retries is not None else config.litellm.max_retries
    tool_spec = load_ingress_distill_tool_spec(config)
    schema = tool_spec_parameters(tool_spec)
    system = render_prompt(PromptPath.INGRESS_DISTILL_SYSTEM).strip()
    user = render_prompt(
        PromptPath.INGRESS_DISTILL_USER,
        channel=envelope.channel.value,
        from_hdr=envelope.from_hdr.value if envelope.from_hdr else "",
        to_hdr=envelope.to_hdr.value if envelope.to_hdr else "",
        subject=envelope.subject.value if envelope.subject else "",
        date=envelope.date.value if envelope.date else "",
        message_id=envelope.message_id.value if envelope.message_id else "",
        in_reply_to=envelope.in_reply_to.value if envelope.in_reply_to else None,
        orphan_notice=(
            envelope.orphan_notice.value if envelope.orphan_notice else None
        ),
        full_body=envelope.full_body.value,
    ).strip()
    call = LiteLlmAcompletionKwargs(
        model=ep.model,
        messages=[
            LiteLlmChatMessage(role="system", content=system),
            LiteLlmChatMessage(role="user", content=user),
        ],
        timeout=float(ep.timeout),
        max_retries=mr,
        api_key=ep.api_key,
        api_base=ep.api_base,
        max_tokens=ep.max_tokens,
        chat_template_kwargs=ep.chat_template_kwargs or None,
    )
    correlation: dict[str, str] | None = None
    if config.e2e.litellm_route_correlation:
        snap = get_litellm_http_correlation()
        if snap is not None:
            correlation = dict(snap)

    last_error: BaseException | None = None
    for attempt in range(_MAX_INGRESS_DISTILL_RETRIES + 1):
        try:
            assistant = invoke_required_tool(
                settings=config,
                call=call,
                tool_spec=tool_spec,
                correlation_snap=correlation,
                context="ingress_distill",
            )
            args = parse_ingress_distill_assistant(assistant, schema=schema)
            parts = ingress_distill_history_parts_from_tool_args(args)
            log.info(
                "ingress_distill_ok",
                history_parts=len(parts),
                user_query_len=len(parts[-1].text),
            )
            return IngressDistillResult(parts=parts)
        except (
            IngressDistillBridgeError,
            LiteLlmToolResponseError,
            jsonschema.ValidationError,
        ) as exc:
            last_error = exc
            if attempt >= _MAX_INGRESS_DISTILL_RETRIES:
                break
            log.warning(
                "ingress_distill_retry",
                attempt=attempt + 1,
                error=str(exc),
            )
    log.warning(
        "ingress_distill_fallback",
        error=str(last_error) if last_error else "unknown",
    )
    trimmed = trim_context_text(
        envelope.full_body.value,
        config.ingress.distill_fallback_max_chars,
    )
    return IngressDistillResult(
        parts=ingress_distill_fallback_history_parts(trimmed)
    )


__all__ = ["ingress_distill_llm", "load_ingress_distill_tool_spec"]
