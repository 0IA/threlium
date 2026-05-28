"""Value Objects для стадий knowledge system (logic_validate, memory_query).

Все payload-структуры — результат ``parse_*`` фабрик из ``threlium.knowledge_fsm``.
"""
from __future__ import annotations

import enum

import msgspec

from ._core import _OptionalStripEmpty


class LogicInferenceMode(enum.StrEnum):
    """Замкнутый набор режимов RDFS/OWL-вывода pySHACL для ``logic_validate``.

    ``value`` совпадает с аргументом ``inference`` ``pyshacl.validate`` (кроме
    :attr:`NONE`, который означает «вывод выключен» → ``None``).
    """

    NONE = "none"
    RDFS = "rdfs"
    OWLRL = "owlrl"
    BOTH = "both"

    def to_pyshacl(self) -> str | None:
        """Значение для ``pyshacl.validate(inference=...)``; :attr:`NONE` → ``None``."""
        return None if self is LogicInferenceMode.NONE else self.value


class LogicValidateStagePayload(msgspec.Struct, frozen=True):
    """Payload после parse body для logic_validate стадии."""

    reasoning: str
    shapes_ttl: str
    facts_ttl: str
    ontology_ttl: str | None = None
    inference: LogicInferenceMode | None = None


class MemoryQueryStagePayload(msgspec.Struct, frozen=True):
    """Payload после parse body для memory_query стадии."""

    reasoning: str
    query: str


class LogicValidateReportText(_OptionalStripEmpty):
    """Обрезанный отчёт pySHACL / syntax error для observation."""


__all__ = [
    "LogicInferenceMode",
    "LogicValidateReportText",
    "LogicValidateStagePayload",
    "MemoryQueryStagePayload",
]
