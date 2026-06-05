"""Явная карта канала → handler ``threlium.bridges.<channel>.run_bridge``."""
from __future__ import annotations

from collections.abc import Callable
from email.message import EmailMessage
from typing import Protocol

from threlium.settings import ThreliumSettings
from threlium.bridges import email as bridge_email
from threlium.bridges import matrix as bridge_matrix
from threlium.bridges import telegram as bridge_telegram
from threlium.bridges.isomorph.run_bridge import run_bridge as isomorph_run_bridge
from threlium.types.bridge_ingress_channel import BridgeIngressChannel


class BridgeRunner(Protocol):
    def __call__(
        self,
        deliver: Callable[[EmailMessage], None],
        *,
        settings: ThreliumSettings,
    ) -> None: ...


BRIDGE_RUNNERS: dict[BridgeIngressChannel, BridgeRunner] = {
    BridgeIngressChannel.EMAIL: bridge_email.run_bridge,
    BridgeIngressChannel.MATRIX: bridge_matrix.run_bridge,
    BridgeIngressChannel.TELEGRAM: bridge_telegram.run_bridge,
    BridgeIngressChannel.ISOMORPH: isomorph_run_bridge,
}

__all__ = ["BridgeRunner", "BRIDGE_RUNNERS"]
