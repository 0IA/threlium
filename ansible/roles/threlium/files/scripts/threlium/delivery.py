"""Запуск внешних бинарников: fdm. notmuch new из FSM не вызывается (см. docs/INDEX.md)."""
from __future__ import annotations

import os
import shutil
import subprocess
from email.message import EmailMessage
from pathlib import Path

from threlium.mail import parse_rfc822, serialize_rfc822_for_wire


def require_exe(which_name: str, default: str, install_hint: str) -> str:
    p = shutil.which(which_name) or default
    if not Path(p).is_file():
        raise RuntimeError(f"{which_name} not found (install {install_hint})")
    return p


def run_fdm(stdin: bytes) -> None:
    """Одно письмо на stdin → ``fdm -m -a stdin fetch`` (``~/.fdm.conf``).

    Вход нормализуется через :mod:`threlium.mail`: :func:`~threlium.mail.parse_rfc822`
    и :func:`~threlium.mail.serialize_rfc822_for_wire`.

    Маршрутизация и ``notmuch insert`` — в fdm.conf; стадия не передаётся из Python.
    При ошибке или коде ≠ 0 — ``RuntimeError``.
    """
    fdm_bin = require_exe("fdm", "/usr/bin/fdm", "fdm")
    env = os.environ.copy()
    env["HOME"] = str(Path.home())
    payload = serialize_rfc822_for_wire(parse_rfc822(stdin))
    proc = subprocess.run(
        [fdm_bin, "-m", "-a", "stdin", "fetch"],
        input=payload,
        capture_output=True,
        env=env,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"fdm exit {proc.returncode}: {err}")


def fdm_bytes_from_message(msg: EmailMessage) -> bytes:
    """Сериализация письма для stdin fdm / ``notmuch insert`` (:func:`~threlium.mail.serialize_rfc822_for_wire`)."""
    return serialize_rfc822_for_wire(msg)
