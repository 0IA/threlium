"""Пути runtime движка FSM."""
from __future__ import annotations

from pathlib import Path

ENGINE_SOCKET_NAME = "threlium-engine.sock"


def engine_socket_path(home: Path) -> Path:
    """``$THRELIUM_HOME/locks/threlium-engine.sock``."""

    return home / "locks" / ENGINE_SOCKET_NAME
