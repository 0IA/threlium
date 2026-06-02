"""Wire-строки email / Matrix / Telegram мостов."""
from __future__ import annotations

from ._core import _OptionalStripEmpty, _SingleLineHeaderWire


def matrix_homeserver_url(raw: str) -> str:
    """Wire homeserver → URL для ``nio.AsyncClient`` (strip, ``https://`` по умолчанию)."""
    s = str(raw).strip().rstrip("/")
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    return s


class BridgeEmailSubjectLine(_SingleLineHeaderWire):
    """Subject входящего письма email-моста (канонизация)."""


class MatrixRoomNameWire(_SingleLineHeaderWire):
    """Имя комнаты Matrix (``m.room.name`` → ``content.name``) после strip на границе sync."""


class MatrixOutboundPlainBodyWire(_OptionalStripEmpty):
    """Plain-тело исходящего ``m.room.message`` (Matrix Client-Server egress)."""


class TelegramBridgeInboundCaptionOrText(_OptionalStripEmpty):
    """Текст или caption входящего сообщения Telegram-моста."""


class TelegramPtbOutboundReplyBody(_OptionalStripEmpty):
    """Исходящий plain-текст ответа PTB (truncate до лимита Telegram)."""
