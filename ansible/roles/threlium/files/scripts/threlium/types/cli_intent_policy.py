"""Политика стадии ``cli_intent`` (sandbox / privileged) и union-решение роутера."""
from __future__ import annotations

from enum import StrEnum

import msgspec

from .fsm_stage import FsmStage


class CliIntentPolicy(StrEnum):
    SANDBOX = "sandbox"
    PRIVILEGED = "privileged"


class CliRouteCollision(msgspec.Struct, frozen=True):
    """``argv`` пытается запустить имя FSM-маршрута как CLI-команду.

    ``route`` — целевая стадия, чьё имя коллидирует с бинарём; ``cmd`` —
    отрендеренная командная строка для текста observation-подсказки.
    """

    route: FsmStage
    cmd: str


class CliExecDecision(msgspec.Struct, frozen=True):
    """Решение об исполнении команды: sandbox (user scope) или privileged (system scope)."""

    policy: CliIntentPolicy


CliIntentDecision = CliRouteCollision | CliExecDecision


__all__ = [
    "CliExecDecision",
    "CliIntentDecision",
    "CliIntentPolicy",
    "CliRouteCollision",
]
