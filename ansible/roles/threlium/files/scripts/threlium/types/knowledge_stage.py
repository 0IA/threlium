"""Value Objects для стадий knowledge system (formal_reason, memory_query).

Все payload-структуры — результат ``parse_*`` фабрик из ``threlium.knowledge_fsm``.
"""
from __future__ import annotations

import enum

import msgspec

from ._core import _OptionalStripEmpty


class LogicInferenceMode(enum.StrEnum):
    """Замкнутый набор режимов RDFS/OWL-вывода pySHACL для ``formal_reason``.

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


class FormalReasonStagePayload(msgspec.Struct, frozen=True):
    """Payload после parse body для formal_reason стадии."""

    reasoning: str
    shapes_ttl: str
    facts_ttl: str
    ontology_ttl: str | None = None
    inference: LogicInferenceMode | None = None
    query: str | None = None
    return_derived: bool = False


class MemoryQueryStagePayload(msgspec.Struct, frozen=True):
    """Payload после parse body для memory_query стадии."""

    reasoning: str
    query: str


class FormalReasonErrorKind(enum.StrEnum):
    """Замкнутый набор фатальных ошибок formal_reason для ветвления в observation.

    :attr:`NONE` (``""``) — фатальной ошибки нет, рендерится блок валидации.
    Ошибки query/derived **не** входят сюда — они supplemental и не затирают
    успешную SHACL-валидацию.
    """

    NONE = ""
    PARSE = "parse"
    SHAPE = "shape"
    RUNTIME = "runtime"


class FormalReasonReportText(_OptionalStripEmpty):
    """Обрезанный отчёт pySHACL для observation."""


class FormalReasonFatalErrorText(_OptionalStripEmpty):
    """Текст фатальной ошибки (parse/shape/runtime) для observation."""


class FormalReasonDerivedTtlText(_OptionalStripEmpty):
    """Секция ``derived_triples`` (Turtle-дельта inference); пусто → секция не рендерится."""


class FormalReasonQueryResultText(_OptionalStripEmpty):
    """Секция ``query_result`` (результат SPARQL); пусто → секция не рендерится."""


class FormalReasonQueryErrorText(_OptionalStripEmpty):
    """Доп. (не фатальная) ошибка SPARQL-запроса; рендерится секцией поверх валидации."""


class FormalReasonDerivedErrorText(_OptionalStripEmpty):
    """Доп. (не фатальная) ошибка построения entailed-дельты."""


class FormalReasonOutcome(enum.StrEnum):
    """Класс исхода ``formal_reason`` для FSM gate и ``<system>`` JSON."""

    PASSED = "passed"
    TECHNICAL_FAILED = "technical_failed"
    SHACL_NEGATIVE = "shacl_negative"


class FormalReasonResultPayload(msgspec.Struct, frozen=True):
    """Исходящий machine payload ``formal_reason`` (``<system>`` на enrich_fast)."""

    outcome: FormalReasonOutcome
    error_kind: FormalReasonErrorKind
    conforms: bool
    violations: int
    has_query_error: bool
    has_derived_error: bool


__all__ = [
    "FormalReasonDerivedErrorText",
    "FormalReasonDerivedTtlText",
    "FormalReasonErrorKind",
    "FormalReasonFatalErrorText",
    "FormalReasonOutcome",
    "FormalReasonResultPayload",
    "FormalReasonQueryErrorText",
    "FormalReasonQueryResultText",
    "FormalReasonReportText",
    "FormalReasonStagePayload",
    "LogicInferenceMode",
    "MemoryQueryStagePayload",
]
