"""response_observe@localhost → enrich_fast@localhost.

Собирает текущее состояние буфера ответа, вызывает LLM для
структурированной суммаризации и передаёт результат в enrich_fast
для возврата в reasoning.
"""
from __future__ import annotations

from email.message import EmailMessage

from threlium.fsm_emit import build_fsm_multipart_to_stage
from threlium.mime_reform import EnrichPartId
from threlium.litellm_client import litellm_site_completion_text
from threlium.logutil import logger
from threlium.prompts import render_prompt
from threlium.response.collect import collect_ops
from threlium.response.state_summary import build_state_data
from threlium.settings import ThreliumSettings
from threlium.types import (
    FsmStage,
    LiteLlmChatMessage,
    LitellmRoutingSite,
    MailHeaderName,
    NotmuchMessageIdInner,
    PromptPath,
    RfcMessageIdWire,
)

log = logger.bind(stage="response_observe")


def _llm_observe(data_kw: dict[str, object], config: ThreliumSettings) -> str:
    """LLM-суммаризация буфера ответа."""
    system = render_prompt(PromptPath.RESPONSE_OBSERVE_SYSTEM).strip()
    user = render_prompt(PromptPath.RESPONSE_OBSERVE_USER, **data_kw).strip()
    return litellm_site_completion_text(
        config,
        LitellmRoutingSite.RESPONSE_OBSERVE,
        [
            LiteLlmChatMessage(role="system", content=system),
            LiteLlmChatMessage(role="user", content=user),
        ],
    )


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
