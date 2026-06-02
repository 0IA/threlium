"""msgspec Struct аргументов tool_calls для фаз LightRAG."""
from __future__ import annotations

import msgspec


class LightragEntityRecord(msgspec.Struct, frozen=True):
    name: str
    type: str
    description: str


class LightragRelationRecord(msgspec.Struct, frozen=True):
    source_entity: str
    target_entity: str
    relationship_keywords: str
    relationship_description: str


class ExtractKnowledgeGraphEntityToolArgs(msgspec.Struct, frozen=True):
    """Args первичного прохода ``extract_knowledge_graph`` (отдельный VO от gleaning)."""

    entities: list[LightragEntityRecord]
    relations: list[LightragRelationRecord]


class ExtractKnowledgeGraphGleaningToolArgs(msgspec.Struct, frozen=True):
    """Args повторного прохода ``extract_knowledge_graph_gleaning`` (пропущенное/исправленное)."""

    entities: list[LightragEntityRecord]
    relations: list[LightragRelationRecord]


class SummarizeDescriptionsToolArgs(msgspec.Struct, frozen=True):
    summary: str


class ExtractQueryKeywordsToolArgs(msgspec.Struct, frozen=True):
    high_level_keywords: list[str]
    low_level_keywords: list[str]


class GenerateRagAnswerToolArgs(msgspec.Struct, frozen=True):
    answer: str


__all__ = [
    "ExtractKnowledgeGraphEntityToolArgs",
    "ExtractKnowledgeGraphGleaningToolArgs",
    "ExtractQueryKeywordsToolArgs",
    "GenerateRagAnswerToolArgs",
    "LightragEntityRecord",
    "LightragRelationRecord",
    "SummarizeDescriptionsToolArgs",
]
