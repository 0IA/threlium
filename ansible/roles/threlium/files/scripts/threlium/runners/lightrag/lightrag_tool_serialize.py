"""Struct/VO → wire-строка для контракта LightRAG ``llm_func``."""
from __future__ import annotations

import json

import msgspec

from threlium.types.lightrag_tool_args import (
    ExtractKnowledgeGraphEntityToolArgs,
    ExtractKnowledgeGraphGleaningToolArgs,
    ExtractQueryKeywordsToolArgs,
    GenerateRagAnswerToolArgs,
    SummarizeDescriptionsToolArgs,
)
from threlium.types.lightrag_tool_wire import (
    LightragEntitySummaryText,
    LightragExtractionJsonText,
    LightragKeywordsJsonText,
    LightragRagAnswerText,
)


def _title_case_name(name: str) -> str:
    """Title-case имён сущностей/концов связи: LightRAG дедупит узлы по имени, а constrained
    decoding не нормализует регистр (json-промпт 1.5 просит title-case для case-insensitive имён)."""
    return " ".join(part[:1].upper() + part[1:] if part else part for part in name.split())


def lightrag_extraction_json_from_args(
    args: ExtractKnowledgeGraphEntityToolArgs | ExtractKnowledgeGraphGleaningToolArgs,
) -> LightragExtractionJsonText:
    """Нативный JSON LightRAG ``{entities, relationships}`` → ``operate._process_json_extraction_result``.

    Принимает оба VO фазы (entity / gleaning) — одинаковая нативная схема. Tool args УЖЕ в нативной форме
    (tool spec = JSON LightRAG, constrained decoding), поэтому сериализация — прямой dump; только title-case
    имён сущностей и концов связи для дедупа узлов LightRAG. Пустые массивы валидны (gleaning завершён).
    """
    payload = {
        "entities": [
            {"name": _title_case_name(e.name), "type": e.type, "description": e.description}
            for e in args.entities
        ],
        "relationships": [
            {
                "source": _title_case_name(r.source),
                "target": _title_case_name(r.target),
                "keywords": r.keywords,
                "description": r.description,
            }
            for r in args.relationships
        ],
    }
    return LightragExtractionJsonText.parse(json.dumps(payload, ensure_ascii=False))


def lightrag_keywords_json_from_args(
    args: ExtractQueryKeywordsToolArgs,
) -> LightragKeywordsJsonText:
    payload = msgspec.to_builtins(args)
    return LightragKeywordsJsonText.parse(json.dumps(payload, ensure_ascii=False))


def lightrag_summary_from_args(
    args: SummarizeDescriptionsToolArgs,
) -> LightragEntitySummaryText:
    return LightragEntitySummaryText.parse(args.summary)


def lightrag_rag_answer_from_args(
    args: GenerateRagAnswerToolArgs,
) -> LightragRagAnswerText:
    return LightragRagAnswerText.parse(args.answer)


__all__ = [
    "lightrag_extraction_json_from_args",
    "lightrag_keywords_json_from_args",
    "lightrag_rag_answer_from_args",
    "lightrag_summary_from_args",
]
