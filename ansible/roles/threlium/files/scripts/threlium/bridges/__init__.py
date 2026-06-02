"""Внешние мосты Threlium: long-running services (Email, Telegram, Matrix).

Не FSM-стадии: запускаются systemd как инстансы ``threlium-bridge@<chan>.service``
(единый шаблон ``threlium-bridge@.service``, ``python -m threlium.runners.bridge <chan>``)
и доставляют сообщения в ``ingress@localhost`` через ``fdm`` / ``notmuch insert``
(атомарная вставка; см. docs/ORCHESTRATION.md §6).
"""
from __future__ import annotations

from email.message import EmailMessage
from email.utils import formatdate

from threlium.fsm_emit import HDR_ROUTE
from threlium.types import (
    IngressRoute,
    IngressRouteB62Wire,
    IrtHashWire,
    MatrixRoomNameWire,
    NotmuchMessageIdInner,
    RfcInReplyToWire,
    RfcMessageIdWire,
    RfcSubjectWire,
    MailHeaderName,
    RawIngressCaptureAttachmentFilename,
    ThreliumSpaceB62Wire,
)

_HDR = MailHeaderName

BridgeInReplyTo = RfcMessageIdWire | RfcInReplyToWire | NotmuchMessageIdInner | None

def matrix_room_name_to_ingress_subject_wire(
    name: MatrixRoomNameWire | None,
) -> RfcSubjectWire | None:
    """``m.room.name`` → :class:`~threlium.types.rfc.RfcSubjectWire` для заголовка bridge→ingress."""
    if name is None:
        return None
    return RfcSubjectWire.parse_present_optional(name.value)


def _bridge_in_reply_to_header_value(v: BridgeInReplyTo) -> str | None:
    if v is None:
        return None
    if isinstance(v, NotmuchMessageIdInner):
        return v.as_angle_bracket_header()
    s = v.value.strip()
    if not s:
        return None
    if not (s.startswith("<") and s.endswith(">")):
        inner = s.strip("<>")
        if not inner:
            return None
        return f"<{inner}>"
    return s


def attach_raw_ingress_capture(msg: EmailMessage, raw_capture: str) -> None:
    """Добавить ``text/plain`` attachment с каноническим ``filename`` (мутация ``msg``)."""
    fn = RawIngressCaptureAttachmentFilename.canonical().value
    msg.add_attachment(
        raw_capture.encode("utf-8"),
        maintype="text",
        subtype="plain",
        filename=fn,
    )


def build_bridge_ingress_email(
    *,
    channel: str,
    body: str,
    route: IngressRoute,
    message_id: str,
    in_reply_to: BridgeInReplyTo = None,
    subject: RfcSubjectWire | None = None,
    raw_capture: str | None = None,
    space_wire: ThreliumSpaceB62Wire | None = None,
) -> EmailMessage:
    """Готовое письмо bridge→ingress (runner только fdm).

    При непустом ``raw_capture`` — второй MIME-часть ``text/plain`` attachment
    (основное тело первым ``text/plain`` для :func:`~threlium.mime_reform.extract_plain_body`).
    """
    msg = EmailMessage()
    route_wire = IngressRouteB62Wire.from_ingress_route(route).value
    msg[_HDR.FROM] = f"{channel}@localhost"
    msg[_HDR.TO] = "ingress@localhost"
    msg[_HDR.DATE] = formatdate(localtime=True)
    msg[_HDR.MESSAGE_ID] = message_id
    if subject is not None:
        msg[_HDR.SUBJECT] = subject.value
    irt = _bridge_in_reply_to_header_value(in_reply_to)
    if irt:
        msg[_HDR.IN_REPLY_TO] = irt
        msg[_HDR.IRT_HASH] = IrtHashWire.from_irt_header_value(irt).value
    msg[HDR_ROUTE] = route_wire
    if space_wire is not None and space_wire.value:
        msg[_HDR.SPACE_HASH] = space_wire.space_hash_wire().value
    msg.set_content(body, subtype="plain", charset="utf-8")
    if raw_capture is not None and raw_capture.strip():
        attach_raw_ingress_capture(msg, raw_capture)
    return msg


__all__ = [
    "attach_raw_ingress_capture",
    "BridgeInReplyTo",
    "build_bridge_ingress_email",
    "matrix_room_name_to_ingress_subject_wire",
]
