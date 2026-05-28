#!/usr/bin/env python3
"""Два тестовых письма в Maildir под union ``stages/`` + ``notmuch new`` + проверка ``In-Reply-To``.

1. Письмо **без** строки заголовка ``In-Reply-To``.
2. Письмо с ``In-Reply-To`` длиннее 256 символов (значение в угловых скобках).

Дальше: ``notmuch new``, поиск по ``id:``, вывод ``notmuch show`` (сырой конверт) и
краткая сверка: есть ли строка ``In-Reply-To`` в проиндексированном показе.

Пример (агент)::

  export NOTMUCH_CONFIG=$HOME/.notmuch-config
  python3 scripts/notmuch_irt_header_maildir_probe.py \\
    --stages-root /home/threlium/threlium/data/stages \\
    --notmuch-new

Каталог писем: ``<stages-root>/irt_header_probe/Maildir/new/`` (создаётся).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
import uuid
from pathlib import Path


def _maildir_unique_name(host: str = "probe.local") -> str:
    return f"{int(time.time())}.P{os.getpid()}_{uuid.uuid4().hex[:10]}.{host}"


def _write_maildir_new(
    dest_dir: Path,
    *,
    message_id_inner: str,
    subject: str,
    in_reply_to_line: str | None,
    body: str,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = _maildir_unique_name()
    path = dest_dir / name
    lines = [
        f"Message-ID: <{message_id_inner}>",
        "From: probe@localhost",
        "To: ingress@localhost",
        f"Subject: {subject}",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=utf-8",
        "Content-Transfer-Encoding: 8bit",
    ]
    if in_reply_to_line is not None:
        lines.append(in_reply_to_line.rstrip("\r\n"))
    lines.extend(["", body])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _notmuch(env: dict[str, str], argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["notmuch", *argv],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _header_block_from_show_raw(stdout: str) -> str:
    """Первый конверт в выводе ``notmuch show --format=raw`` до пустой строки перед телом."""
    parts = re.split(r"\n\n", stdout, maxsplit=1)
    return parts[0] if parts else stdout


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--stages-root",
        type=Path,
        default=None,
        help="Корень union stages (например $THRELIUM_HOME/stages). Иначе env THRELIUM_HOME/stages",
    )
    ap.add_argument(
        "--notmuch-config",
        type=Path,
        default=None,
        help="Путь к notmuch-config. Иначе NOTMUCH_CONFIG или ~/.notmuch-config",
    )
    ap.add_argument(
        "--notmuch-new",
        action="store_true",
        help="Выполнить ``notmuch new`` после записи файлов",
    )
    args = ap.parse_args()

    stages = args.stages_root
    if stages is None:
        home = os.environ.get("THRELIUM_HOME", "").strip()
        if not home:
            ap.error("Задайте --stages-root или переменную окружения THRELIUM_HOME")
        stages = Path(home) / "stages"
    stages = stages.resolve()

    nm_cfg = args.notmuch_config
    if nm_cfg is None:
        nm_cfg = Path(os.environ.get("NOTMUCH_CONFIG", Path.home() / ".notmuch-config"))
    nm_cfg = nm_cfg.expanduser().resolve()

    new_dir = stages / "irt_header_probe" / "Maildir" / "new"
    mid_no_irt = f"irt-probe-no-irt-{int(time.time())}@probe.local"
    mid_long_irt = f"irt-probe-long-irt-{int(time.time())}@probe.local"
    long_inner = "a" * 250 + "@long.local"
    long_irt_value = f"<{long_inner}>"
    assert len(long_irt_value) > 256, len(long_irt_value)
    in_reply_to_line = f"In-Reply-To: {long_irt_value}"

    env = os.environ.copy()
    env["NOTMUCH_CONFIG"] = str(nm_cfg)

    print("=== paths ===")
    print("stages_root:", stages)
    print("new_dir:", new_dir)
    print("NOTMUCH_CONFIG:", nm_cfg)
    print()

    p1 = _write_maildir_new(
        new_dir,
        message_id_inner=mid_no_irt,
        subject="irt probe: no In-Reply-To header line",
        in_reply_to_line=None,
        body="Body A: no In-Reply-To header at all.\n",
    )
    p2 = _write_maildir_new(
        new_dir,
        message_id_inner=mid_long_irt,
        subject="irt probe: long In-Reply-To",
        in_reply_to_line=in_reply_to_line,
        body=(
            "Body B: In-Reply-To angle value length = "
            f"{len(long_irt_value)} chars (>256).\n"
        ),
    )
    print("=== written ===")
    print(p1)
    print(p2)
    print("In-Reply-To line (only msg2):\n ", in_reply_to_line)
    print("len(In-Reply-To value in brackets):", len(long_irt_value))
    print()

    if args.notmuch_new:
        r = _notmuch(env, ["new"])
        print("=== notmuch new ===")
        print("rc:", r.returncode)
        if r.stdout.strip():
            print(r.stdout.rstrip())
        if r.stderr.strip():
            print(r.stderr.rstrip())

    for label, mid in (
        ("no_irt", mid_no_irt),
        ("long_irt", mid_long_irt),
    ):
        term = f"id:{mid}"
        print(f"=== {label} {term} ===")
        rs = _notmuch(env, ["search", "--output=files", term])
        print("search --output=files rc:", rs.returncode, "stdout:", repr(rs.stdout.strip()))
        sh = _notmuch(env, ["show", "--format=raw", term])
        print("show --format=raw rc:", sh.returncode)
        block = _header_block_from_show_raw(sh.stdout)
        has_key = any(
            line.lower().startswith("in-reply-to:")
            for line in block.splitlines()
        )
        irt_lines = [ln for ln in block.splitlines() if ln.lower().startswith("in-reply-to:")]
        print("header block has In-Reply-To line:", has_key)
        if irt_lines:
            for ln in irt_lines:
                val = ln.split(":", 1)[1].strip() if ":" in ln else ln
                print(" In-Reply-To line length:", len(ln), "value length:", len(val))
                print(" ", ln[:200] + ("…" if len(ln) > 200 else ""))
        else:
            print(" (no In-Reply-To lines in notmuch show raw header block)")
        print()


if __name__ == "__main__":
    main()
