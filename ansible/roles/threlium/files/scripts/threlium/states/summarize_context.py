#!/usr/bin/env python3
"""summarize_context@localhost: LLM-суммаризация + тегирование оригиналов.

Получает JSON-задание от enrich с mids и телами писем, вызывает LLM score 0
для суммаризации, тегирует оригиналы tag:context_summarized и передаёт
plain text summary в summarize_memory (стадия-хранитель).
"""
from __future__ import annotations

from email.message import EmailMessage

import msgspec

from threlium import nm as nmlib
from threlium.fsm_emit import build_fsm_step_to_stage
from threlium.litellm_correlation_headers import fsm_correlation_snap
from threlium.litellm_required_tool import build_site_call, invoke_required_tool
from threlium.litellm_tool_spec import load_tool_spec
from threlium.logutil import logger
from threlium.mime_reform import system_part_text
from threlium.prompts import render_prompt
from threlium.settings import ThreliumSettings
from threlium.summarize_tool_bridge import parse_summarize_thread_context_assistant
from threlium.types import (
    EnrichUserQueryText,
    FsmStage,
    LiteLlmChatMessage,
    LitellmCallSite,
    LitellmRoutingSite,
    NotmuchMessageIdInner,
    NotmuchTag,
    PromptPath,
    SummarizeContextStagePayload,
    SummarizeToolBridgeError,
    validated_user_query,
)


def _parse_payload(text: str) -> tuple[list[str], list[str], EnrichUserQueryText] | None:
    """payload → ``(mids, bodies, user_query)``.

    Через msgspec (TYPES § stage payload), без ``json.loads`` + ручного ``dict``.
    Невалидный/пустой batch → ``None``.
    """
    try:
        payload = msgspec.json.decode(
            text.strip().encode("utf-8"), type=SummarizeContextStagePayload
        )
    except (msgspec.DecodeError, msgspec.ValidationError):
        return None
    batch = payload.summarize
    if not batch.mids:
        return None
    try:
        user_query = validated_user_query(payload)
    except ValueError:
        return None
    return (list(batch.mids), list(batch.bodies), user_query)


log = logger.bind(stage="summarize_context")


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    body_raw = system_part_text(msg).strip()
    parsed = _parse_payload(body_raw)
    if parsed is None:
        log.error("unparseable_payload", body_preview=body_raw[:200])
        return None

    mids, bodies, user_query = parsed
    log.info("summarizing", message_count=len(mids))

    system = render_prompt(PromptPath.SUMMARIZE_CONTEXT_SYSTEM).strip()
    user = render_prompt(
        PromptPath.SUMMARIZE_CONTEXT_USER,
        message_count=len(bodies),
        bodies=bodies,
    ).strip()

    call = build_site_call(
        config,
        LitellmRoutingSite.SUMMARIZE_CONTEXT,
        [
            LiteLlmChatMessage(role="system", content=system),
            LiteLlmChatMessage(role="user", content=user),
        ],
    )
    tool_spec = load_tool_spec(PromptPath.SUMMARIZE_CONTEXT_TOOL_SPEC)
    # Fail-fast симметрично enrich overflow (CONTEXT_CONTRACT §5): при провале tool bridge
    # или пустой сводке оригиналы НЕ тегируются context_summarized — иначе они выпали бы из
    # unified без замены, потеряв хвост треда. Тег ставится только после валидной сводки.
    try:
        assistant = invoke_required_tool(
            settings=config,
            call=call,
            tool_spec=tool_spec,
            correlation_snap=fsm_correlation_snap(
                msg, config, LitellmCallSite.SUMMARIZE_THREAD_CONTEXT
            ),
            context="summarize_thread_context",
        )
        summary = parse_summarize_thread_context_assistant(assistant).summary
    except SummarizeToolBridgeError as exc:
        log.error("summarize_tool_bridge_failed", error=str(exc))
        raise RuntimeError(
            "summarize_context: tool bridge failed; originals left untagged"
        ) from exc
    if not summary.strip():
        log.error("empty_summary", message_count=len(mids))
        raise RuntimeError(
            "summarize_context: empty summary; originals left untagged"
        )

    nm_mids = [NotmuchMessageIdInner.parse(m) for m in mids]
    tagged = nmlib.batch_tag_add(nm_mids, NotmuchTag.CONTEXT_SUMMARIZED)
    log.info("tagged_originals", tagged=tagged, total=len(nm_mids))

    # Сводка едет <history>-частью: оригиналы помечены context_summarized (выпадают из
    # unified), поэтому именно эта history-копия заменяет их в контексте следующего enrich.
    # user_query релеится в <system>: summarize_memory отдаст его enrich как <history>, чтобы
    # re-trigger повторил тот же ход пользователя (суммаризация его не меняет, CONTEXT §5).
    return build_fsm_step_to_stage(
        msg,
        to_addr=FsmStage.SUMMARIZE_MEMORY,
        from_stage=stage,
        history=summary,
        system=user_query.value,
        settings=config,
    )
