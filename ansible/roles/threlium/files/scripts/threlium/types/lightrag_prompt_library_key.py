"""Ключи ``lightrag.prompt.PROMPTS``, которые Threlium подменяет overlay'ем.

Значение члена enum — wire-имя ключа в словаре библиотеки; путь шаблона
``prompts/lightrag/<value>.j2`` — через :meth:`LightragPromptLibraryKey.prompt_path`.
"""
from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from threlium.types.prompt_path import PromptPath


class LightragPromptLibraryKey(StrEnum):
    ENTITY_EXTRACTION_SYSTEM_PROMPT = "entity_extraction_system_prompt"
    ENTITY_EXTRACTION_USER_PROMPT = "entity_extraction_user_prompt"
    ENTITY_CONTINUE_EXTRACTION_USER_PROMPT = "entity_continue_extraction_user_prompt"
    ENTITY_EXTRACTION_EXAMPLES = "entity_extraction_examples"
    SUMMARIZE_ENTITY_DESCRIPTIONS = "summarize_entity_descriptions"
    FAIL_RESPONSE = "fail_response"
    RAG_RESPONSE = "rag_response"
    NAIVE_RAG_RESPONSE = "naive_rag_response"
    KG_QUERY_CONTEXT = "kg_query_context"
    NAIVE_QUERY_CONTEXT = "naive_query_context"
    KEYWORDS_EXTRACTION = "keywords_extraction"
    KEYWORDS_EXTRACTION_EXAMPLES = "keywords_extraction_examples"

    def prompt_path(self) -> PromptPath:
        """Шаблон ``lightrag/<ключ>.j2`` как член :class:`~threlium.types.prompt_path.PromptPath`."""
        from threlium.types.prompt_path import PromptPath  # noqa: PLC0415 — цикл с ``prompt_path``

        _LIGHTRAG_KEY_TO_PROMPT_PATH: dict[LightragPromptLibraryKey, PromptPath] = {
            LightragPromptLibraryKey.ENTITY_EXTRACTION_SYSTEM_PROMPT: PromptPath.LIGHTRAG_ENTITY_EXTRACTION_SYSTEM_PROMPT,
            LightragPromptLibraryKey.ENTITY_EXTRACTION_USER_PROMPT: PromptPath.LIGHTRAG_ENTITY_EXTRACTION_USER_PROMPT,
            LightragPromptLibraryKey.ENTITY_CONTINUE_EXTRACTION_USER_PROMPT: PromptPath.LIGHTRAG_ENTITY_CONTINUE_EXTRACTION_USER_PROMPT,
            LightragPromptLibraryKey.ENTITY_EXTRACTION_EXAMPLES: PromptPath.LIGHTRAG_ENTITY_EXTRACTION_EXAMPLES,
            LightragPromptLibraryKey.SUMMARIZE_ENTITY_DESCRIPTIONS: PromptPath.LIGHTRAG_SUMMARIZE_ENTITY_DESCRIPTIONS,
            LightragPromptLibraryKey.FAIL_RESPONSE: PromptPath.LIGHTRAG_FAIL_RESPONSE,
            LightragPromptLibraryKey.RAG_RESPONSE: PromptPath.LIGHTRAG_RAG_RESPONSE,
            LightragPromptLibraryKey.NAIVE_RAG_RESPONSE: PromptPath.LIGHTRAG_NAIVE_RAG_RESPONSE,
            LightragPromptLibraryKey.KG_QUERY_CONTEXT: PromptPath.LIGHTRAG_KG_QUERY_CONTEXT,
            LightragPromptLibraryKey.NAIVE_QUERY_CONTEXT: PromptPath.LIGHTRAG_NAIVE_QUERY_CONTEXT,
            LightragPromptLibraryKey.KEYWORDS_EXTRACTION: PromptPath.LIGHTRAG_KEYWORDS_EXTRACTION,
            LightragPromptLibraryKey.KEYWORDS_EXTRACTION_EXAMPLES: PromptPath.LIGHTRAG_KEYWORDS_EXTRACTION_EXAMPLES,
        }
        return _LIGHTRAG_KEY_TO_PROMPT_PATH[self]


__all__ = ["LightragPromptLibraryKey"]
