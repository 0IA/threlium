"""Канал входа моста (slug в ``X-Threlium-Route`` / ``IngressRouterResolvedChannelSlug``)."""
from __future__ import annotations

from enum import StrEnum


class BridgeIngressChannel(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"
    MATRIX = "matrix"
    ISOMORPH = "isomorph"
