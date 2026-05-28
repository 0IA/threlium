"""Парсинг и политика CLI FSM (cli_intent / cli_resume / cli_hitl_out)."""
from __future__ import annotations

import json
import os
import re

import msgspec

from threlium.settings import ThreliumSettings
from threlium.types import (
    CliIntentPayload,
    CliIntentPolicy,
)


def parse_json_loose(text: str) -> object:
    text = text.strip()
    m = re.search(r"\{[\s\S]*\}\s*$", text)
    if m:
        text = m.group(0)
    return json.loads(text)


def parse_cli_intent_payload(text: str) -> CliIntentPayload | None:
    """Ожидается {\"cli\": {\"argv\": [str, ...], \"cwd\": str?}}."""
    obj = parse_json_loose(text)
    if not isinstance(obj, dict):
        return None
    cli = obj.get("cli")
    if not isinstance(cli, dict):
        return None
    argv = cli.get("argv")
    if not isinstance(argv, list) or not argv:
        return None
    if not all(isinstance(x, str) for x in argv):
        return None
    cwd_raw = cli.get("cwd")
    cwd_norm: str | None
    if cwd_raw is None:
        cwd_norm = None
    elif isinstance(cwd_raw, str):
        t = cwd_raw.strip()
        cwd_norm = t if t else None
    else:
        return None
    try:
        return msgspec.convert(
            {"argv": argv, "cwd": cwd_norm},
            type=CliIntentPayload,
        )
    except msgspec.ValidationError:
        return None


def cli_payload_as_json(cli: CliIntentPayload) -> str:
    inner: dict[str, object] = {"argv": cli.argv}
    if cli.cwd:
        inner["cwd"] = cli.cwd
    return json.dumps({"cli": inner}, ensure_ascii=False)


def _deny_substrings(settings: ThreliumSettings) -> tuple[str, ...]:
    """Подстроки, запрещённые в строке ``" ".join(argv)`` (не в отдельных аргументах).

    Список задаётся ``settings.cli.deny_patterns`` (через запятую) или дефолтом ниже.
    Проверка намеренно грубая — ради жёсткой безопасности.
    """
    raw = settings.cli.deny_patterns.strip()
    if raw:
        return tuple(s.strip() for s in raw.split(",") if s.strip())
    return (";", "|", "`", "$(", "${", "&&", "||", "\n", "\r")


def _allowlist_basenames(settings: ThreliumSettings) -> set[str]:
    raw = settings.cli.allowlist
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def classify_cli_policy(cli: CliIntentPayload, settings: ThreliumSettings) -> CliIntentPolicy:
    """Политика исполнения CLI: ``allow`` | ``deny`` | ``hitl``.

    Все элементы ``argv`` склеиваются в одну строку; если в ней встречается любая
    подстрока из `_deny_substrings()` — ``deny``. Иначе, если basename ``argv[0]``
    в allowlist (``THRELIUM_CLI_ALLOWLIST``) — ``allow``, иначе — ``hitl``.

    Осознанно возможны ложные ``deny`` (например ``;`` внутри аргумента ``echo``).
    """
    argv = cli.argv
    joined = " ".join(argv)
    for sub in _deny_substrings(settings):
        if sub in joined:
            return CliIntentPolicy.DENY
    base = os.path.basename(argv[0].strip() or " ").lower()
    if base in _allowlist_basenames(settings):
        return CliIntentPolicy.ALLOW
    return CliIntentPolicy.HITL


def parse_yes_no(text: str) -> bool | None:
    """True = да, False = нет, None = неоднозначно (обрабатываем как отказ)."""
    line = text.strip().split("\n", 1)[0].strip().lower()
    if re.match(r"^(yes|y|да|д)\s*\.?$", line):
        return True
    if re.match(r"^(no|n|нет|н)\s*\.?$", line):
        return False
    return None
