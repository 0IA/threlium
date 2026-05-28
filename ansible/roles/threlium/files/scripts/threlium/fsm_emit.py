"""FSM-билдеры MIME: EmailMessage → EmailMessage (docs/FSM.md §5)."""
from __future__ import annotations


from collections.abc import Mapping
from email.message import EmailMessage
from email.utils import formatdate
from typing import Protocol, TypeAlias

from threlium.settings import ThreliumSettings
from threlium.mime_reform import EnrichPartId, _make_inline_text_part
from threlium.types import (
    CanonicalMidWire,
    FsmPlainToStageSubjectLine,
    FsmStage,
    FsmTransitionPlainBody,
    FsmTransitionPlainSubjectLine,
    HopBudgetLine,
    IrtHashWire,
    NotmuchMessageIdInner,
    RfcInReplyToWire,
    RfcMessageIdWire,
    ThreliumCapabilitiesBudgetLine,
    MailHeaderName,
)


class StateHandler(Protocol):
    """Контракт ``threlium.states.<stage>.main`` (keyword-only ``config`` — см. воркер)."""

    def __call__(
        self,
        msg: EmailMessage,
        stage: FsmStage,
        *,
        config: ThreliumSettings,
    ) -> EmailMessage | None: ...


HDR_ROUTE = MailHeaderName.ROUTE.value
HDR_HOP_BUDGET = MailHeaderName.HOP_BUDGET.value
HDR_CAPABILITIES = MailHeaderName.CAPABILITIES.value
HDR_FROM = MailHeaderName.FROM.value
HDR_TO = MailHeaderName.TO.value
HDR_SUBJECT = MailHeaderName.SUBJECT.value
HDR_DATE = MailHeaderName.DATE.value
HDR_MESSAGE_ID = MailHeaderName.MESSAGE_ID.value
HDR_IN_REPLY_TO = MailHeaderName.IN_REPLY_TO.value
HDR_IRT_HASH = MailHeaderName.IRT_HASH.value


ManagedFsmHeaderValue: TypeAlias = (
    RfcInReplyToWire | HopBudgetLine | ThreliumCapabilitiesBudgetLine
)
ManagedFsmHeaderPatch: TypeAlias = Mapping[MailHeaderName, ManagedFsmHeaderValue]


def _default_root_hop_max(settings: ThreliumSettings) -> int:
    return settings.hop.budget_root


def _default_sub_hop_max(settings: ThreliumSettings) -> int:
    return settings.hop.budget_sub


def _default_root_capability(settings: ThreliumSettings) -> str:
    return settings.cap.root or "root"


def _default_sub_capability(settings: ThreliumSettings) -> str:
    return settings.cap.sub or "L1"


def advance_hop_budget_for_simple_step(line: HopBudgetLine, settings: ThreliumSettings) -> HopBudgetLine:
    """Декремент хвоста hop-стека: ``'48 44'`` → ``'48 43'``, ``'47'`` → ``'46'``."""
    parts = line.value.split() if line.value else [str(_default_root_hop_max(settings))]
    parts[-1] = str(max(0, int(parts[-1]) - 1))
    return HopBudgetLine.parse(" ".join(parts))


def push_subagent_hop_budget(line: HopBudgetLine, settings: ThreliumSettings) -> HopBudgetLine | None:
    """PUSH: декремент хвоста + append(sub_max). ``None`` если хвост после step < 1."""
    parts = line.value.split() if line.value else [str(_default_root_hop_max(settings))]
    new_tail = int(parts[-1]) - 1
    if new_tail < 1:
        return None
    parts[-1] = str(new_tail)
    return HopBudgetLine.parse(" ".join(parts + [str(_default_sub_hop_max(settings))]))


def push_subagent_capability(line: HopBudgetLine, settings: ThreliumSettings) -> ThreliumCapabilitiesBudgetLine:
    r = line.value
    sub = _default_sub_capability(settings)
    root = _default_root_capability(settings)
    if not r:
        return ThreliumCapabilitiesBudgetLine.parse(f"{root} {sub}")
    return ThreliumCapabilitiesBudgetLine.parse(f"{r} {sub}")


def hop_budget_remaining(line: HopBudgetLine, settings: ThreliumSettings) -> int:
    """Оставшийся бюджет текущего уровня (хвост стека). ``0`` = исчерпан."""
    parts = line.value.split() if line.value else []
    if not parts:
        return _default_root_hop_max(settings)
    tail = parts[-1]
    try:
        return max(0, int(tail))
    except ValueError:
        raise RuntimeError(
            f"FSM-инвариант: X-Threlium-Hop-Budget tail is not an integer: {tail!r} "
            f"(full line: {line.value!r})"
        )


def irt_wire_from_incoming_message_id(incoming: EmailMessage) -> RfcInReplyToWire | None:
    """``In-Reply-To`` из ``Message-ID`` входящего письма (эквивалент прежнего ``prev_mid`` в emit)."""
    mid_w = RfcMessageIdWire.parse_present_from_email(incoming, HDR_MESSAGE_ID)
    prev_mid = _msgid_normalized(mid_w.value if mid_w is not None else None)
    return RfcInReplyToWire.parse_present_optional(prev_mid) if prev_mid else None


def _msgid_normalized(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    if s.startswith("<") and s.endswith(">"):
        return s
    return f"<{s.strip('<> ')}>"


def _apply_managed_headers(
    out: EmailMessage,
    managed_headers: Mapping[MailHeaderName, ManagedFsmHeaderValue] | None,
) -> None:
    """Записать только переданные managed VO (без дефолтов с входа)."""
    if not managed_headers:
        return
    for name, vo in managed_headers.items():
        if name == MailHeaderName.IN_REPLY_TO:
            if not isinstance(vo, RfcInReplyToWire):
                raise TypeError(
                    f"{MailHeaderName.IN_REPLY_TO} expects RfcInReplyToWire, got {type(vo).__name__}"
                )
            if vo.value.strip():
                irt_hdr = _msgid_normalized(vo.value)
                if irt_hdr:
                    out[HDR_IN_REPLY_TO] = irt_hdr
                    out[HDR_IRT_HASH] = IrtHashWire.from_irt_header_value(irt_hdr).value
        elif name == MailHeaderName.HOP_BUDGET:
            if not isinstance(vo, HopBudgetLine):
                raise TypeError(
                    f"{MailHeaderName.HOP_BUDGET} expects HopBudgetLine, got {type(vo).__name__}"
                )
            if vo.value.strip():
                out[HDR_HOP_BUDGET] = vo.value
        elif name == MailHeaderName.CAPABILITIES:
            if not isinstance(vo, ThreliumCapabilitiesBudgetLine):
                raise TypeError(
                    f"{MailHeaderName.CAPABILITIES} expects ThreliumCapabilitiesBudgetLine, "
                    f"got {type(vo).__name__}"
                )
            if vo.value.strip():
                out[HDR_CAPABILITIES] = vo.value
        else:
            raise ValueError(f"unsupported managed header key: {name!r}")


def emit_transition_preserving_payload(
    incoming: EmailMessage,
    *,
    to_addr: FsmStage,
    from_stage: FsmStage,
    managed_headers: ManagedFsmHeaderPatch | None = None,
) -> EmailMessage:
    """Новое RFC822 с тем же MIME-телом, что у входа; обновляет envelope и managed по карте.

    Whitelist-подход: в новом письме только заголовки из
    :meth:`MailHeaderName.propagate_from_incoming` (Subject) + явно пересобранные
    (From, To, Date, Message-ID) + записанные из ``managed_headers``
    (``MailHeaderName`` → VO). Дефолты «IRT из MID входа», «advance hop»
    не применяются — используйте обёртки в :mod:`threlium.fsm_emit_semantic`.
    """
    # --- Body ---
    out = EmailMessage()
    payload = incoming.get_payload(decode=False)
    if incoming.is_multipart() and isinstance(payload, list):
        out.set_payload(payload)
        ct = incoming.get(MailHeaderName.CONTENT_TYPE) or incoming.get_content_type() or "multipart/mixed"
        out[MailHeaderName.CONTENT_TYPE] = ct
        mv = incoming.get(MailHeaderName.MIME_VERSION)
        if mv:
            out[MailHeaderName.MIME_VERSION] = mv
    else:
        raw = incoming.get_payload(decode=True)
        if isinstance(raw, bytes):
            charset = incoming.get_content_charset() or "utf-8"
            subtype = (incoming.get_content_subtype() or "plain").lower()
            out.set_content(raw.decode(charset, errors="replace"), subtype=subtype, charset=charset)
        else:
            out.set_content("" if payload is None else str(payload), subtype="plain", charset="utf-8")

    # --- Propagate whitelist (Subject) ---
    for hdr in MailHeaderName.propagate_from_incoming():
        v = incoming.get(hdr)
        if v is not None:
            out[hdr] = v

    # --- Rebuilt (envelope) ---
    out[HDR_FROM] = from_stage.rfc822_mailbox
    out[HDR_TO] = to_addr.rfc822_mailbox
    out[HDR_DATE] = formatdate(localtime=True)
    mid_new = RfcMessageIdWire.internal_for_fsm()
    CanonicalMidWire.assert_from_wire(mid_new)
    out[HDR_MESSAGE_ID] = mid_new.value

    _apply_managed_headers(out, managed_headers)

    return out


def build_fsm_plain_to_stage(
    incoming: EmailMessage,
    *,
    to_addr: FsmStage,
    from_stage: FsmStage,
    body: FsmTransitionPlainBody,
    subject_line: FsmTransitionPlainSubjectLine | None = None,
    message_id_wire: RfcMessageIdWire | None = None,
    settings: ThreliumSettings,
) -> EmailMessage:
    """Новое text/plain письмо на ``to_addr``; тредовые заголовки от входа
    (``docs/FSM.md`` §5.1). Канал определяется в ``egress_router``
    по ``X-Threlium-Route``.

    ``message_id_wire``: опционально для стадий, которым нужен заранее известный
    ``Message-ID``.
    """
    mid_w = RfcMessageIdWire.parse_present_from_email(incoming, HDR_MESSAGE_ID)
    irt_inner = NotmuchMessageIdInner.from_optional_wire(mid_w)
    irt = irt_inner.as_angle_bracket_header() if irt_inner is not None else None
    subj = FsmPlainToStageSubjectLine.parse(incoming.get(HDR_SUBJECT)).value

    out_subj_raw = subject_line.value if subject_line is not None else f"Re: {subj}"
    out_subj = out_subj_raw.replace("\n", " ").replace("\r", "")[:900]
    out = EmailMessage()
    out[HDR_FROM] = from_stage.rfc822_mailbox
    out[HDR_TO] = to_addr.rfc822_mailbox
    out[HDR_SUBJECT] = out_subj
    out[HDR_DATE] = formatdate(localtime=True)
    mid_new = message_id_wire if message_id_wire is not None else RfcMessageIdWire.internal_for_fsm()
    CanonicalMidWire.assert_from_wire(mid_new)
    out[HDR_MESSAGE_ID] = mid_new.value
    if irt:
        out[HDR_IN_REPLY_TO] = irt
        out[HDR_IRT_HASH] = IrtHashWire.from_irt_header_value(irt).value
    out.set_content(body.value.strip(), subtype="plain", charset="utf-8")

    v = incoming.get(HDR_CAPABILITIES)
    if v:
        out[HDR_CAPABILITIES] = v

    out[HDR_HOP_BUDGET] = advance_hop_budget_for_simple_step(
        HopBudgetLine.parse(incoming.get(HDR_HOP_BUDGET)), settings
    ).value

    return out


def build_fsm_multipart_to_stage(
    incoming: EmailMessage,
    *,
    to_addr: FsmStage,
    from_stage: FsmStage,
    parts: list[tuple[EnrichPartId, str]],
    settings: ThreliumSettings,
) -> EmailMessage:
    """Новое multipart/mixed письмо с Content-ID частями на ``to_addr``.

    Тредовые заголовки — от входа (как ``build_fsm_plain_to_stage``).
    ``parts``: список ``(EnrichPartId, text)`` — каждая пара становится
    inline text/plain MIME-частью с ``Content-ID``.
    """
    mid_w = RfcMessageIdWire.parse_present_from_email(incoming, HDR_MESSAGE_ID)
    irt_inner = NotmuchMessageIdInner.from_optional_wire(mid_w)
    irt = irt_inner.as_angle_bracket_header() if irt_inner is not None else None
    subj = FsmPlainToStageSubjectLine.parse(incoming.get(HDR_SUBJECT)).value

    out = EmailMessage()
    out.make_mixed()
    out[HDR_FROM] = from_stage.rfc822_mailbox
    out[HDR_TO] = to_addr.rfc822_mailbox
    out[HDR_SUBJECT] = f"Re: {subj}".replace("\n", " ").replace("\r", "")[:900]
    out[HDR_DATE] = formatdate(localtime=True)
    mid_new = RfcMessageIdWire.internal_for_fsm()
    CanonicalMidWire.assert_from_wire(mid_new)
    out[HDR_MESSAGE_ID] = mid_new.value
    if irt:
        out[HDR_IN_REPLY_TO] = irt
        out[HDR_IRT_HASH] = IrtHashWire.from_irt_header_value(irt).value

    for part_id, text in parts:
        out.attach(_make_inline_text_part(part_id, text))

    v = incoming.get(HDR_CAPABILITIES)
    if v:
        out[HDR_CAPABILITIES] = v

    out[HDR_HOP_BUDGET] = advance_hop_budget_for_simple_step(
        HopBudgetLine.parse(incoming.get(HDR_HOP_BUDGET)), settings
    ).value

    return out
