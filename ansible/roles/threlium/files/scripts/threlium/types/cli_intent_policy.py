"""Политика стадии ``cli_intent`` (allow / deny / hitl)."""
from __future__ import annotations

from enum import StrEnum


class CliIntentPolicy(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    HITL = "hitl"
