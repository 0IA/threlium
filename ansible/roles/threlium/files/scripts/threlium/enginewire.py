"""Лёгкий wire-слой UNIX-engine: протокол submit + статус ``threlium-work@``-инстанса.

**Намеренно без зависимости от пакета ``threlium.types``.** Его ``__init__`` реэкспортит
весь домен и через ``_core``/``litellm_tool_call``/``enrich→reasoning`` тянет ``litellm``
(~1.5 c импорта). Этот модуль грузится на **каждый** FSM-hop: ``threlium-work@<stage>``
запускает ``python -m threlium.runners.engine_submit``, который лишь сериализует один
wire-запрос и ждёт ответ движка. Импорт litellm на каждый hop добавлял ~2 c латентности
диспетчеризации (многошаговые потоки выходили за poll-таймауты e2e и тормозили прод).

Граница модуля — структурная гарантия «лёгкости»: единственная зависимость — ``msgspec``;
тяжёлый импорт сюда нельзя добавить, не сломав диспетчеризацию (в отличие от хрупких
``TYPE_CHECKING``-гардов в листовых модулях, которые молча ломает транзитивный импорт).
См. ``docs/ORCHESTRATION.md`` (диспетчеризация work@/sweep@) и ``docs/TYPES.md`` (уровень 1).

Богатые статусы движка/мостов/LightRAG живут в ``threlium.types.systemd_status``
(долгоживущие процессы — одноразовая стоимость импорта); здесь — только ``work_*``,
нужные submitter'у.
"""
from __future__ import annotations

from typing import Annotated, Literal, Self

import msgspec

NonEmptyStr = Annotated[str, msgspec.Meta(min_length=1)]


class EngineWireRequest(msgspec.Struct, frozen=True):
    """Тело запроса: ``stage`` (local-part стадии), ``thread_id`` (суффикс треда notmuch)."""

    stage: str
    thread_id: str

    @classmethod
    def from_work_instance(cls, instance: str) -> Self:
        """Разбор systemd instance ``%i`` = ``<stage>:<thread_id>``."""

        stage, sep, thread_id = instance.partition(":")
        if not sep or not stage or not thread_id:
            raise ValueError(f"invalid work instance: {instance!r}")
        return cls(stage=stage, thread_id=thread_id)


class EngineWireOk(msgspec.Struct, frozen=True):
    status: Literal["ok"]


class EngineWireError(msgspec.Struct, frozen=True):
    status: Literal["error"]
    message: str
    traceback: str


class WorkStatusBody(msgspec.Struct, frozen=True, kw_only=True):
    """Wire ``STATUS=`` (sd_notify) для жизненного цикла ``threlium-work@``-инстанса.

    Лёгкий аналог ``SystemdStatusBody`` (тот же контракт VO: strip + непустота),
    но без зависимости от ``threlium.types`` — submitter не платит за тяжёлый импорт.
    """

    value: NonEmptyStr

    @classmethod
    def _require(cls, raw: str) -> Self:
        text = raw.strip()
        if not text:
            raise ValueError("empty work status")
        return cls(value=text)

    @classmethod
    def work_waiting_for_engine(cls, *, work_instance: str) -> Self:
        return cls._require(f"Work {work_instance}: waiting for engine")

    @classmethod
    def work_failed_socket(cls, *, work_instance: str) -> Self:
        return cls._require(
            f"Work {work_instance}: failed (cannot connect to engine socket)"
        )

    @classmethod
    def work_failed_engine_error(cls, *, work_instance: str) -> Self:
        return cls._require(f"Work {work_instance}: failed (engine error)")

    @classmethod
    def work_done(cls, *, work_instance: str) -> Self:
        return cls._require(f"Work {work_instance}: done")
