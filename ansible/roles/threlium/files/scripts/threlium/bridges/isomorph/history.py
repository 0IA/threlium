"""Stateless tail-extractor + контент-адресуемые Message-ID (ядро isomorph-моста).

Структурный разбор ``messages`` (НЕ поиск подстроки), БЕЗ чтения notmuch:
  1. найти последний ``role=="assistant"`` (= прошлый ответ Threlium);
  2. ``In-Reply-To = canon(IsomorphContentId(hash(last_assistant)))`` — MID glue прошлого хода
     (egress сминтил его так же) → notmuch свяжет тред сам;
  3. хвост = сообщения после last-assistant (tool-результаты + возможный новый user) → один ingress;
  4. ``Message-ID = canon(IsomorphContentId(hash(хвост, parent)))`` — идемпотентность + позиционная
     уникальность.

Нет last-assistant → первый ход: ``In-Reply-To=None``, хвост = последний user-turn.

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


class TailExtraction(msgspec.Struct, frozen=True):
    """Результат разбора запроса для построения одного ingress-письма."""

    #: ``In-Reply-To`` нового ingress (MID glue прошлого хода) или ``None`` для первого хода.
    in_reply_to: RfcMessageIdWire | None
    #: Контент-адресуемый ``Message-ID`` нового ingress.
    message_id: RfcMessageIdWire
    #: Plain-текст хвоста → ``<system>``-body для FSM (bridge→ingress только ``<system>``).
    body: str


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


def extract_tail(surface: IsomorphApiSurface, body: dict[str, object]) -> TailExtraction:
    """Полная история Cline → (In-Reply-To, Message-ID, body) для одного ingress. Чистый compute."""
    msgs = _parse_messages(surface, body)

    last_assistant_idx = -1
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].role == "assistant" and msgs[i].assistant is not None:
            last_assistant_idx = i
            break

    if last_assistant_idx < 0:
        # Первый ход: родителя нет; хвост = последний user-turn (фолбэк — все user/tool).
        in_reply_to = None
        parent_value = ""
        tail_msgs = _first_turn_tail(msgs)
    else:
        anchor = msgs[last_assistant_idx].assistant
        assert anchor is not None
        in_reply_to = _content_addressed_mid(
            IsomorphContentHashWire.from_content(anchor).value
        )
        parent_value = in_reply_to.value
        tail_msgs = msgs[last_assistant_idx + 1:]

    body_text = "\n".join(m.render for m in tail_msgs if m.render).strip()
    message_id = _content_addressed_mid(
        IsomorphContentHashWire.from_ingress_tail(parent=parent_value, tail=body_text).value
    )
    return TailExtraction(in_reply_to=in_reply_to, message_id=message_id, body=body_text)


def _first_turn_tail(msgs: list[_Msg]) -> list[_Msg]:
    """Первый ход: предпочесть последний user; если его нет — все не-system сообщения."""
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].role == "user":
            return [msgs[i]]
    return [m for m in msgs if m.role != "system"]
