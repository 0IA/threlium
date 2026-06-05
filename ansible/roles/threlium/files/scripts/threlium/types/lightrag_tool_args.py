"""msgspec Struct аргументов tool_calls для фаз LightRAG."""
from __future__ import annotations

import msgspec


class LightragEntityRecord(msgspec.Struct, frozen=True):
    name: str
    type: str
    description: str


class LightragRelationRecord(msgspec.Struct, frozen=True):
    # Поля = НАТИВНАЯ JSON-схема LightRAG 1.5 (operate._process_json_extraction_result:
    # rel_data.get("source"/"target"/"keywords"/"description")). tool spec = эта схема → LLM при
    # tool_choice=required (vLLM constrained decoding) генерит сразу валидный нативный JSON, без
    # промежуточной конвертации.
    source: str
    target: str
    keywords: str
    description: str


class ExtractKnowledgeGraphEntityToolArgs(msgspec.Struct, frozen=True):
    """Args первичного прохода ``extract_knowledge_graph`` = нативный JSON LightRAG (`entities`/`relationships`)."""

    entities: list[LightragEntityRecord]
    relationships: list[LightragRelationRecord]


class ExtractKnowledgeGraphGleaningToolArgs(msgspec.Struct, frozen=True):
    """Args повторного прохода ``extract_knowledge_graph_gleaning`` = тот же нативный JSON LightRAG."""

    entities: list[LightragEntityRecord]
    relationships: list[LightragRelationRecord]


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
