"""Однострочный текст для systemd ``STATUS=`` (sd_notify).

Граница — VO, не литералы в FSM/раннерах (`docs/TYPES.md` §2).
"""
from __future__ import annotations

from pathlib import Path
from typing import Self

from threlium.types._core import _RequiredNonEmpty
from threlium.types.bridge_ingress_channel import BridgeIngressChannel
from threlium.types.fsm_stage import FsmStage
from threlium.types.notmuch import NotmuchThreadScopeId

_VO = "systemd_status"


def _short_thread_scope(scope: NotmuchThreadScopeId) -> str:
    t = scope.value.strip()
    if len(t) <= 20:
        return t
    return t[:16] + "..."


def _short_room_id(room_id: str) -> str:
    t = room_id.strip()
    if len(t) <= 24:
        return t
    return t[:20] + "..."


class SystemdStatusBody(_RequiredNonEmpty):
    """Wire одной строки для полезной нагрузки ``STATUS=`` после strip и проверки непустоты."""

    @classmethod
    def engine_home_configured(cls) -> Self:
        return cls.require(name=_VO, raw="Engine: home configured")

    @classmethod
    def engine_config_loaded(cls) -> Self:
        return cls.require(name=_VO, raw="Engine: config loaded")

    @classmethod
    def engine_starting_lightrag_loop(cls) -> Self:
        return cls.require(name=_VO, raw="Engine: starting LightRAG loop")

    @classmethod
    def engine_preparing_socket(cls, sock_path: Path) -> Self:
        return cls.require(
            name=_VO,
            raw=f"Engine: preparing socket {sock_path}",
        )

    @classmethod
    def engine_listening_on(cls, sock_path: Path) -> Self:
        return cls.require(
            name=_VO,
            raw=f"Engine: listening on {sock_path}",
        )

    @classmethod
    def engine_stopping(cls) -> Self:
        return cls.require(name=_VO, raw="Engine: stopping")

    @classmethod
    def engine_idle_waiting_fsm_requests(cls) -> Self:
        return cls.require(
            name=_VO,
            raw="Engine: idle, waiting for FSM requests",
        )

    @classmethod
    def engine_idle_no_unread(cls) -> Self:
        return cls.require(name=_VO, raw="Engine: idle (no unread in thread)")

    @classmethod
    def engine_fsm_processing(cls, *, stage: FsmStage, thread_scope: NotmuchThreadScopeId) -> Self:
        tid = _short_thread_scope(thread_scope)
        return cls.require(
            name=_VO,
            raw=f"Engine: FSM stage={stage.value} thread={tid}",
        )

    @classmethod
    def engine_fsm_error(cls, *, message: str) -> Self:
        return cls.require(name=_VO, raw=f"Engine: FSM error: {message}")

    @classmethod
    def engine_idle(cls) -> Self:
        return cls.require(name=_VO, raw="Engine: idle")

    @classmethod
    def lightrag_thread_starting(cls) -> Self:
        return cls.require(name=_VO, raw="LightRAG: thread starting")

    @classmethod
    def lightrag_initializing_storages(cls) -> Self:
        return cls.require(name=_VO, raw="LightRAG: initializing storages")

    @classmethod
    def lightrag_storages_ready(cls) -> Self:
        return cls.require(name=_VO, raw="LightRAG: storages ready")

    @classmethod
    def lightrag_boot_failed(cls, *, message: str) -> Self:
        return cls.require(name=_VO, raw=f"LightRAG: boot failed: {message}")

    @classmethod
    def lightrag_idle_no_pending(cls) -> Self:
        return cls.require(name=_VO, raw="LightRAG: idle (no pending)")

    @classmethod
    def lightrag_indexing_batch(cls, *, batch_size: int) -> Self:
        return cls.require(
            name=_VO,
            raw=f"LightRAG: indexing batch size={batch_size}",
        )

    @classmethod
    def lightrag_idle_indexed(cls, *, message_count: int) -> Self:
        return cls.require(
            name=_VO,
            raw=f"LightRAG: idle (indexed {message_count} msgs)",
        )

    @classmethod
    def bridge_channel_starting(cls, channel: BridgeIngressChannel) -> Self:
        return cls.require(
            name=_VO,
            raw=f"Bridge {channel.value}: starting",
        )

    @classmethod
    def bridge_email_connected_idle(cls, *, host: str, port: int) -> Self:
        return cls.require(
            name=_VO,
            raw=f"Bridge email: connected, idle ({host}:{port})",
        )

    @classmethod
    def bridge_email_delivering_uid(cls, *, uid: str) -> Self:
        return cls.require(
            name=_VO,
            raw=f"Bridge email: delivering uid={uid}",
        )

    @classmethod
    def bridge_email_connected_idle_simple(cls) -> Self:
        return cls.require(name=_VO, raw="Bridge email: connected, idle")

    @classmethod
    def bridge_telegram_delivering(
        cls, *, chat_id: str, message_id: int
    ) -> Self:
        return cls.require(
            name=_VO,
            raw=f"Bridge telegram: delivering chat={chat_id} msg={message_id}",
        )

    @classmethod
    def bridge_telegram_connected_idle(cls) -> Self:
        return cls.require(name=_VO, raw="Bridge telegram: connected, idle")

    @classmethod
    def bridge_matrix_connected_idle(cls) -> Self:
        return cls.require(name=_VO, raw="Bridge matrix: connected, idle")

    @classmethod
    def bridge_matrix_delivering_room(cls, *, room_id: str) -> Self:
        rid = _short_room_id(room_id)
        return cls.require(
            name=_VO,
            raw=f"Bridge matrix: delivering room={rid}",
        )

    @classmethod
    def work_waiting_for_engine(cls, *, work_instance: str) -> Self:
        return cls.require(
            name=_VO,
            raw=f"Work {work_instance}: waiting for engine",
        )

    @classmethod
    def work_failed_socket(cls, *, work_instance: str) -> Self:
        return cls.require(
            name=_VO,
            raw=f"Work {work_instance}: failed (cannot connect to engine socket)",
        )

    @classmethod
    def work_failed_engine_error(cls, *, work_instance: str) -> Self:
        return cls.require(
            name=_VO,
            raw=f"Work {work_instance}: failed (engine error)",
        )

    @classmethod
    def work_done(cls, *, work_instance: str) -> Self:
        return cls.require(name=_VO, raw=f"Work {work_instance}: done")
