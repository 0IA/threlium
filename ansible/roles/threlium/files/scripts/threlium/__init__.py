"""Threlium FSM: Python-стадии-функции и общие хелперы.

Публичный контракт стадии — ``main(msg: EmailMessage, stage: FsmStage, *, settings: ThreliumSettings) -> EmailMessage | None``.
Воркер (``threlium.runners.engine``) вызывает handler in-process.

Инициализация рантайма (``THRELIUM_HOME``, ``PATH`` с ``.venv/bin``,
``env/threlium.env``) выполняется systemd unit'ом — ``EnvironmentFile=``
и ``Environment=`` (см. ``docs/ORCHESTRATION.md §6``).
Конфигурация загружается через ``threlium.settings.load_settings()``.
"""
from __future__ import annotations
