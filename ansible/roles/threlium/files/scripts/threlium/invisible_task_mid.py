"""Кодирование целочисленного идентификатора (например task mid) в невидимый суффикс UTF-8.

Используется для неразрушающей метки в конце текста мессенджера (Telegram / Matrix и т.д.).
В алфавите намеренно нет bidi-override, мягкого переноса и «широких» пробелов.

Префикс суффикса: ``U+FFF9`` (Interlinear Annotation Anchor) + ``U+200B`` (ZWSP как щит
для variation selectors у конца видимого текста). Символ ``U+FFF9`` не входит в
16-ричный алфавит, поэтому граница однозначна даже когда нагрузка начинается с
``U+200B`` (hex-цифра ``0``).
"""
from __future__ import annotations

from typing import Final

# 16 символов для base-16: без управления направлением текста и без ширины/переносов.
SAFE_INVISIBLE_ALPHABET: Final[tuple[str, ...]] = (
    "\u200b",  # Zero Width Space
    "\u200c",  # Zero Width Non-Joiner
    "\u200d",  # Zero Width Joiner
    "\u2060",  # Word Joiner
    "\u2061",  # Function Application
    "\u2062",  # Invisible Times
    "\u2063",  # Invisible Separator
    "\u2064",  # Invisible Plus
    "\ufeff",  # Byte Order Mark / ZWNBSP (в середине/конце строки; см. потребителей)
    "\ufe00",  # Variation Selector-1
    "\ufe01",  # Variation Selector-2
    "\ufe02",  # Variation Selector-3
    "\ufe03",  # Variation Selector-4
    "\ufe04",  # Variation Selector-5
    "\ufe05",  # Variation Selector-6
    "\ufe0f",  # Variation Selector-16
)

_SHIELD: Final[str] = "\u200b"
# Якорь вне алфавита — без него rfind(ZWSP) ломается для hex-цифры 0 (индекс 0 = ZWSP).
_SUFFIX_PREFIX: Final[str] = "\ufff9" + _SHIELD

_ALPHABET_INDEX: Final[dict[str, int]] = {ch: i for i, ch in enumerate(SAFE_INVISIBLE_ALPHABET)}


def encode_mid_safe(task_mid: int) -> str:
    """Вернуть невидимый суффикс для ``task_mid`` (неотрицательный int).

    Суффикс нужно дописывать в конец сообщения; декодирование ищет последний такой суффикс.
    """
    if task_mid < 0:
        raise ValueError(f"task_mid must be non-negative, got {task_mid}")
    hex_str = f"{task_mid:x}"
    encoded_payload = "".join(SAFE_INVISIBLE_ALPHABET[int(d, 16)] for d in hex_str)
    return f"{_SUFFIX_PREFIX}{encoded_payload}"


def decode_mid_safe(text: str) -> int | None:
    """Извлечь ``task_mid`` из последнего суффикса ``encode_mid_safe`` в ``text``.

    Возвращает ``None``, если маркера нет, нагрузка пуста или встретился чужой символ.
    """
    start = text.rfind(_SUFFIX_PREFIX)
    if start == -1:
        return None
    invisible_payload = text[start + len(_SUFFIX_PREFIX) :]
    if not invisible_payload:
        return None
    digits: list[str] = []
    for ch in invisible_payload:
        idx = _ALPHABET_INDEX.get(ch)
        if idx is None:
            return None
        digits.append(f"{idx:x}")
    try:
        return int("".join(digits), 16)
    except ValueError:
        return None


PLACEHOLDER_MARKER_INT: Final[int] = 0x7E3A_C91D
"""Фиксированная int-константа маркера placeholder-сообщений egress Telegram.

Ответы на такое сообщение bridge отбрасывает через :func:`is_egress_placeholder_message`
(не через ``decode_mid_safe`` на полном тексте: после невидимого блока идёт видимый
хвост ZWSP + U+231B HOURGLASS, и декодер остановился бы на первом «чужом» символе).
"""


def is_egress_placeholder_message(text: str) -> bool:
    """Текст сообщения — egress placeholder (невидимый маркер проекта в начале)."""
    return text.startswith(encode_mid_safe(PLACEHOLDER_MARKER_INT))


PLACEHOLDER_TEXT: Final[str] = encode_mid_safe(PLACEHOLDER_MARKER_INT) + "\u200b\u231b"
"""Текст placeholder-сообщения: невидимый маркер + видимый hourglass (⌛)."""
