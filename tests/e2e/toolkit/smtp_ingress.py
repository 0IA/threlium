"""SMTP inject into GreenMail fetchmail path."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from tests.e2e.sut_user_systemd import (
    E2E_THRELIUM_USER,
    e2e_threlium_user_unit_journalctl_bash,
)

from .constants import E2E_REMOTE_POSIX_HOME, REPO_ROOT, TIMEOUT_POLL_SHORT
from .greenmail import wait_for_greenmail_ready
from .poll import _diag

def smtp_inject_inbound(
    project_name: str,
    *,
    checkout: str,
    repo_root: Path | None = None,
    message_id: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    in_reply_to: str | None = None,
) -> None:
    """Отправляет письмо в GreenMail по SMTP с хоста pytest (localhost:mapped_port)."""
    del checkout
    started = time.monotonic()
    _diag("smtp inject start")
    root = repo_root or REPO_ROOT
    script = root / "tests" / "e2e" / "smtp_inject.py"
    host, port = wait_for_greenmail_ready(project_name, timeout=TIMEOUT_POLL_SHORT)

    cmd: list[str] = [sys.executable, str(script), host, str(port)]
    if message_id is not None:
        cmd += ["--message-id", message_id]
    if subject is not None:
        cmd += ["--subject", subject]
    if body is not None:
        cmd += ["--body", body]
    if in_reply_to is not None:
        cmd += ["--in-reply-to", in_reply_to]

    r = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=int(TIMEOUT_POLL_SHORT),
    )
    if r.returncode != 0:
        raise RuntimeError(f"smtp inject from host failed: {r.stdout}{r.stderr}")
    _diag(f"smtp inject done (elapsed={time.monotonic() - started:.1f}s)")
    out = (r.stdout or "").strip()
    if out:
        _diag(f"smtp inject host stdout: {out[:500]}")


def _email_bridge_systemd_diag_script() -> str:
    """Снимок юнита bridge-email (Python IMAP IDLE bridge)."""
    return f"""\
set +e
echo "=== threlium-bridge@.service (unit file) ==="
cat {E2E_REMOTE_POSIX_HOME}/.config/systemd/user/threlium-bridge@.service 2>&1 || true
echo "=== journalctl --user-unit threlium-bridge@email.service (runuser {E2E_THRELIUM_USER}, last 120) ==="
{e2e_threlium_user_unit_journalctl_bash("threlium-bridge@email.service", 120)}
echo "=== journalctl broad tail (root, last 40) ==="
journalctl -n 40 --no-pager 2>&1 || true
"""
