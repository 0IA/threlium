#!/usr/bin/env python3
"""egress_telegram@localhost: доставка plain-текста в Telegram Bot API."""
from __future__ import annotations

import json
from email.message import EmailMessage

from telegram import Message as TelegramMessage

from threlium.delivery import run_fdm
from threlium.egress_self_archive import (
    build_egress_sent_record_to_archive,
    find_existing_egress_archive,
)
from threlium.ingress_route_resolve import (
    resolve_egress_task_route_ancestor,
    resolve_egress_task_route_ancestor_with_thread_correlation,
)
from threlium.invisible_task_mid import PLACEHOLDER_TEXT
from threlium.logutil import logger
from threlium.mail import serialize_rfc822_for_wire
from threlium.mime_reform import system_part_text
from threlium.bridges.telegram import (
    edit_message_text,
    run_ptb,
    send_placeholder_text,
    telegram_native_id_from_sent_message,
)
from threlium.bridges.telegram_bot import telegram_bot
from threlium.settings import ThreliumSettings
from threlium.types import (
    FsmStage,
    IngressRoute,
    RfcMessageIdWire,
    TelegramIngressRoute,
    TelegramNativeId,
    TelegramPtbOutboundReplyBody,
    MailHeaderName,
)
from threlium.types.litellm_correlation_header import (
    LitellmCorrelationHeader,
    thread_root_hash,
)

_HDR = MailHeaderName

log = logger.bind(stage="egress_telegram")


async def _send_placeholder(
    config: ThreliumSettings,
    routing: TelegramIngressRoute,
    *,
    correlation_headers: dict[str, str] | None = None,
) -> TelegramMessage:
    async with telegram_bot(config, correlation_headers=correlation_headers) as bot:
        return await send_placeholder_text(bot, routing, PLACEHOLDER_TEXT)


async def _edit_final_text(
    config: ThreliumSettings,
    *,
    chat_id: int,
    message_id: int,
    text: str,
    correlation_headers: dict[str, str] | None = None,
) -> TelegramMessage:
    async with telegram_bot(config, correlation_headers=correlation_headers) as bot:
        return await edit_message_text(
            bot,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
        )


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    def _wrong_type(r: IngressRoute) -> str:
        return (
            "egress_telegram: ожидался TelegramIngressRoute, получен "
            f"{type(r).__name__} (channel={r.channel!r})"
        )

    correlation_headers: dict[str, str] | None = None
    if config.e2e.litellm_route_correlation:
        routing, _snap, thread_resolved = (
            resolve_egress_task_route_ancestor_with_thread_correlation(
                msg,
                TelegramIngressRoute,
                wrong_route_type_message=_wrong_type,
            )
        )
        correlation_headers = {
            LitellmCorrelationHeader.THREAD_ROOT_MID.value:
                thread_root_hash(thread_resolved.message_id_inner.as_angle_bracket_header()),
        }
    else:
        routing, _snap = resolve_egress_task_route_ancestor(
            msg,
            TelegramIngressRoute,
            wrong_route_type_message=_wrong_type,
        )

    body_wire = TelegramPtbOutboundReplyBody.parse_present_optional(
        system_part_text(msg)
    )
    if body_wire is None:
        raise RuntimeError("egress_telegram: plain body is empty after strip")

    final_text = body_wire.value

    existing = find_existing_egress_archive(msg)
    if existing is not None:
        log.info("archive_found_edit_placeholder")
        native = RfcMessageIdWire.native_from_canonical_str(
            existing.glue_message_id.value, TelegramNativeId,
        )
        run_ptb(_edit_final_text(
            config,
            chat_id=native.chat_id,
            message_id=native.message_id,
            text=final_text,
            correlation_headers=correlation_headers,
        ))
        return None

    log.info("sending_placeholder", chat_id=routing.chat_id, reply_to_message_id=routing.message_id)
    placeholder_msg = run_ptb(_send_placeholder(
        config, routing,
        correlation_headers=correlation_headers,
    ))
    log.info("placeholder_sent")

    glue_native = telegram_native_id_from_sent_message(placeholder_msg)
    glue_mid = RfcMessageIdWire.from_native(glue_native)

    sent_raw = json.dumps(
        {
            "channel": "telegram",
            "chat_id": routing.chat_id,
            "message_id": routing.message_id,
            "sent_message_id": placeholder_msg.message_id,
            "text": final_text,
        },
        ensure_ascii=False,
        indent=2,
    )
    archive_email = build_egress_sent_record_to_archive(
        msg, stage=stage, sent_raw=sent_raw, glue_message_id_wire=glue_mid,
        settings=config,
    )
    run_fdm(serialize_rfc822_for_wire(archive_email))
    log.info("archive_written")

    run_ptb(_edit_final_text(
        config,
        chat_id=glue_native.chat_id,
        message_id=glue_native.message_id,
        text=final_text,
        correlation_headers=correlation_headers,
    ))
    log.info("placeholder_edited_to_final")
    return None
