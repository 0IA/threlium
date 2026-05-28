"""response_observe@localhost → enrich_fast@localhost.

Собирает текущее состояние буфера ответа, вызывает LLM для
структурированной суммаризации и передаёт результат в enrich_fast
для возврата в reasoning.
"""
from __future__ import annotations

from email.message import EmailMessage

from litellm.types.utils import Message

from threlium.fsm_emit import build_fsm_multipart_to_stage
from threlium.mime_reform import EnrichPartId
from threlium.litellm_client import litellm_completion_sync
from threlium.litellm_wire import require_chat_model_response
from threlium.logutil import logger
from threlium.prompts import render_prompt
from threlium.response.collect import collect_ops
from threlium.response.state_summary import build_state_data
from threlium.settings import ThreliumSettings, resolve_llm_endpoint
from threlium.types import (
    FsmStage,
    LiteLlmAcompletionKwargs,
    LiteLlmChatMessage,
    LitellmRoutingSite,
    MailHeaderName,
    NotmuchMessageIdInner,
    PromptPath,
    RfcMessageIdWire,
    lite_llm_acompletion_to_dict,
)

log = logger.bind(stage="response_observe")


def _llm_observe(data_kw: dict[str, object], config: ThreliumSettings) -> str:
    """LLM-суммаризация буфера ответа."""
    ep = resolve_llm_endpoint(config.litellm, LitellmRoutingSite.RESPONSE_OBSERVE)
    mr = ep.max_retries if ep.max_retries is not None else config.litellm.max_retries
    log.info("litellm_routing", site=LitellmRoutingSite.RESPONSE_OBSERVE.value, score=ep.score)

    system = render_prompt(PromptPath.RESPONSE_OBSERVE_SYSTEM).strip()
    user = render_prompt(PromptPath.RESPONSE_OBSERVE_USER, **data_kw).strip()

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
    kwargs = lite_llm_acompletion_to_dict(call)
    resp = require_chat_model_response(
        litellm_completion_sync(settings=config, **kwargs, stream=False)
    )
    choice = resp.choices[0]
    msg_obj: Message | None = choice.message
    if msg_obj is not None:
        raw_c = msg_obj.content
        if isinstance(raw_c, str) and raw_c.strip():
            return raw_c.strip()
    return ""


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    mid_w = RfcMessageIdWire.parse_present_from_email(msg, MailHeaderName.MESSAGE_ID.value)
    inner = NotmuchMessageIdInner.from_optional_wire(mid_w)
    if inner is None:
        raise RuntimeError("response_observe: no Message-ID on incoming message")

    ops = collect_ops(inner)
    data = build_state_data(ops)

    data_kw: dict[str, object] = {
        "is_empty": data.is_empty,
        "live_count": data.live_count,
        "total_chars": data.total_chars,
        "chunks": [
            {"position": c.position, "content": c.content, "deleted": c.deleted}
            for c in data.chunks
        ],
        "deleted_positions": [c.position for c in data.chunks if c.deleted],
    }

    observation = _llm_observe(data_kw, config)
    log.info(
        "observed",
        ops_count=len(ops),
        observation_chars=len(observation),
        message_id=mid_w.value if mid_w else None,
    )

    return build_fsm_multipart_to_stage(
        msg,
        to_addr=FsmStage.ENRICH_FAST,
        from_stage=stage,
        parts=[(EnrichPartId.PLAN_STATE, observation)],
        settings=config,
    )
