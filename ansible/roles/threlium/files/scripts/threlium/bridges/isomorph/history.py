"""Разбор присланной истории клиента: кандидаты-голоса + хвост (чистый compute, БЕЗ notmuch).

Структурный разбор ``messages`` (НЕ поиск подстроки):
  1. найти последний ``role=="assistant"`` (= прошлый ответ Threlium); ассистента нет → первый ход;
  2. кандидаты = ``G_i = canon(IsomorphContentId(hash(R_i)))`` КАЖДОГО assistant-ответа, most-recent-first;
  3. хвост = сообщения после last-assistant (tool-результаты + возможный новый user) → один ``<system>``.

``In-Reply-To`` НЕ считается здесь: его резолвит :mod:`.thread_resolve` голосованием по кандидатам в
notmuch (устойчивость к коллизии последнего ответа + детект «прошлый ход ещё в работе»). Финальный
``Message-ID`` нового ingress (``hash(parent=IRT, tail)``) — :func:`ingress_message_id` после резолва.

Нормализация контента ассистента (для паритета хеша с ``states/egress_isomorph``) — общий модуль
:mod:`threlium.types.isomorph_content`.
"""
from __future__ import annotations

import msgspec

from threlium.types import (
    IsomorphApiSurface,
    IsomorphAssistantContent,
    IsomorphContentHashWire,
    IsomorphContentId,
    IsomorphToolCallSig,
    RfcMessageIdWire,
    canonical_json,
)


class ParsedHistory(msgspec.Struct, frozen=True):
    """Чистый разбор присланной истории (без notmuch): кандидаты на голосование + хвост."""

    #: ``G_i = canon(hash(R_i))`` последних assistant-ответов, **most-recent-first**; для голосования
    #: за целевой тред (:mod:`.thread_resolve`). Пусто ⟺ первый ход (ассистента в истории нет).
    recent_assistant_mids: tuple[RfcMessageIdWire, ...]
    #: Plain-текст хвоста (после last-assistant) → ``<system>``-body для FSM.
    tail_body: str


# --- внутренние нормализованные представления --------------------------------------------


class _Msg(msgspec.Struct, frozen=True):
    role: str
    #: Для assistant — нейтральный контент для хеша; иначе ``None``.
    assistant: IsomorphAssistantContent | None
    #: Plain-рендер для FSM-body (любая роль).
    render: str


def _coerce_text(content: object) -> str:
    """OpenAI/Anthropic ``content``: строка или массив блоков → плоский текст."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type")
                if btype in ("text", "input_text", "output_text"):
                    parts.append(str(block.get("text", "")))
                elif btype == "tool_result":
                    parts.append(_coerce_text(block.get("content")))
        return "\n".join(p for p in parts if p)
    return str(content)


def _openai_assistant_content(m: dict[str, object]) -> IsomorphAssistantContent:
    text = _coerce_text(m.get("content")).strip()
    tool_calls: list[IsomorphToolCallSig] = []
    raw_tc = m.get("tool_calls")
    if isinstance(raw_tc, list):
        for tc in raw_tc:
            if not isinstance(tc, dict):
                continue
            fn_raw = tc.get("function")
            fn: dict[str, object] = fn_raw if isinstance(fn_raw, dict) else {}
            name = str(fn.get("name", ""))
            args = fn.get("arguments", "")
            # OpenAI усекает tool-call id на resend → НЕ включаем (tool_id="").
            tool_calls.append(
                IsomorphToolCallSig(name=name, arguments=_canon_args(args), tool_id="")
            )
    return IsomorphAssistantContent(text=text, tool_calls=tuple(tool_calls))


def _anthropic_assistant_content(m: dict[str, object]) -> IsomorphAssistantContent:
    texts: list[str] = []
    tool_calls: list[IsomorphToolCallSig] = []
    content = m.get("content")
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                texts.append(str(block.get("text", "")))
            elif btype == "tool_use":
                # Anthropic tool_use.id echo-стабилен → включаем для уникальности.
                tool_calls.append(
                    IsomorphToolCallSig(
                        name=str(block.get("name", "")),
                        arguments=_canon_args(block.get("input")),
                        tool_id=str(block.get("id", "")),
                    )
                )
    return IsomorphAssistantContent(
        text="\n".join(t for t in texts if t).strip(), tool_calls=tuple(tool_calls)
    )


def _canon_args(args: object) -> str:
    """Аргументы tool-вызова (строка JSON или объект) → каноническая JSON-строка."""
    if isinstance(args, str):
        s = args.strip()
        if not s:
            return ""
        try:
            return canonical_json(msgspec.json.decode(s.encode("utf-8")))
        except msgspec.DecodeError:
            return canonical_json(s)
    return canonical_json(args)


def _render_user_or_tool(role: str, m: dict[str, object]) -> str:
    text = _coerce_text(m.get("content")).strip()
    return f"[{role}] {text}" if text else ""


def _parse_messages(surface: IsomorphApiSurface, body: dict[str, object]) -> list[_Msg]:
    raw = body.get("messages")
    if not isinstance(raw, list):
        raise ValueError("isomorph: request body has no 'messages' array")
    out: list[_Msg] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "")).strip()
        if role == "assistant":
            if surface is IsomorphApiSurface.ANTHROPIC_MESSAGES:
                content = _anthropic_assistant_content(m)
            else:
                content = _openai_assistant_content(m)
            render = content.text
            out.append(_Msg(role=role, assistant=content, render=render))
        else:
            out.append(_Msg(role=role, assistant=None, render=_render_user_or_tool(role, m)))
    return out


def _content_addressed_mid(content_hash: str) -> RfcMessageIdWire:
    return RfcMessageIdWire.from_native(IsomorphContentId(v=1, content_hash=content_hash))


def _render_body(tail_msgs: list[_Msg]) -> str:
    return "\n".join(m.render for m in tail_msgs if m.render).strip()


def parse_history(surface: IsomorphApiSurface, body: dict[str, object]) -> ParsedHistory:
    """Полная история клиента → кандидаты-голоса (G_i, most-recent-first) + хвост. Чистый compute.

    Хвост = сообщения после последнего assistant (или первый user-turn, если ассистента нет) —
    сливаются в один ``<system>``-body. Кандидаты — ``G_i`` каждого assistant-ответа, свежайший первым;
    их сверяет с notmuch :func:`threlium.bridges.isomorph.thread_resolve.resolve_in_reply_to`.
    """
    msgs = _parse_messages(surface, body)

    last_assistant_idx = -1
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].role == "assistant" and msgs[i].assistant is not None:
            last_assistant_idx = i
            break

    if last_assistant_idx < 0:
        return ParsedHistory(recent_assistant_mids=(), tail_body=_render_body(_first_turn_tail(msgs)))

    candidates: list[RfcMessageIdWire] = []
    for i in range(last_assistant_idx, -1, -1):  # most-recent-first
        a = msgs[i].assistant
        if msgs[i].role == "assistant" and a is not None:
            candidates.append(_content_addressed_mid(IsomorphContentHashWire.from_content(a).value))

    return ParsedHistory(
        recent_assistant_mids=tuple(candidates),
        tail_body=_render_body(msgs[last_assistant_idx + 1:]),
    )


def ingress_message_id(*, parent_value: str, tail_body: str) -> RfcMessageIdWire:
    """Контент-адресуемый ``Message-ID`` нового ingress = ``hash(parent=IRT, tail)``.

    ``parent_value`` = resolved ``In-Reply-To`` (``G_j``) или ``""`` для orphan. Идемпотентность ретраев
    + позиционная уникальность (тот же хвост под разным родителем → разные MID).
    """
    return _content_addressed_mid(
        IsomorphContentHashWire.from_ingress_tail(parent=parent_value, tail=tail_body).value
    )


def _first_turn_tail(msgs: list[_Msg]) -> list[_Msg]:
    """Первый ход: предпочесть последний user; если его нет — все не-system сообщения."""
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].role == "user":
            return [msgs[i]]
    return [m for m in msgs if m.role != "system"]
