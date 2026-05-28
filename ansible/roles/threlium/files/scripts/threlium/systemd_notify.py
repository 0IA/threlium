"""Уведомления systemd через ``NOTIFY_SOCKET`` (протокол ``sd_notify``).

Без PyPI: один datagram на abstract/path UNIX-socket. Без ``NOTIFY_SOCKET`` — no-op.
Текст ``STATUS=`` — только через :class:`~threlium.types.systemd_status.SystemdStatusBody`
и :func:`notify_status` (см. ``docs/TYPES.md``).
"""
from __future__ import annotations

import os
import socket

from threlium.types.systemd_status import SystemdStatusBody


def ensure_systemd_user_env() -> None:
    """``XDG_RUNTIME_DIR`` и user D-Bus для ``threlium-work@`` (как в shell submit)."""

    uid = os.getuid()
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        bus = os.path.join(os.environ["XDG_RUNTIME_DIR"], "bus")
        if os.path.exists(bus):
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"


def _truncate_line(s: str, max_len: int = 120) -> str:
    t = s.replace("\n", " ").replace("\r", " ").strip()
    if len(t) <= max_len:
        return t
    if max_len <= 3:
        return t[:max_len]
    return t[: max_len - 3] + "..."


def systemd_notify(message: str) -> None:
    """Полная строка протокола (``READY=1``, ``STOPPING=1``) без префикса ``STATUS=``."""
    raw = os.environ.get("NOTIFY_SOCKET")
    if not raw:
        return
    path = raw
    if path.startswith('"') and path.endswith('"') and len(path) >= 2:
        path = path[1:-1]
    payload = message.encode("utf-8")
    if not payload.endswith(b"\n"):
        payload += b"\n"
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            if path.startswith("@"):
                sock.connect("\0" + path[1:])
            else:
                sock.connect(path)
            sock.send(payload)
        finally:
            sock.close()
    except OSError as e:
        from threlium.logutil import logger

        logger.bind(stage="systemd_notify").warning("notify_failed", error=str(e))


def notify_ready() -> None:
    systemd_notify("READY=1")


def notify_stopping() -> None:
    systemd_notify("STOPPING=1")


def notify_status(body: SystemdStatusBody) -> None:
    """Отправить ``STATUS=`` для VO-тела статуса (усечение только здесь)."""
    systemd_notify("STATUS=" + _truncate_line(body.value))
