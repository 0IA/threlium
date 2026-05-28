"""Overlay для ``lightrag.prompt.PROMPTS`` из шаблонов ``prompts/lightrag/*.j2``.

Тела для ключей :class:`~threlium.types.lightrag_prompt_library_key.LightragPromptLibraryKey`
хранятся **дословно** как в ``lightrag.prompt.PROMPTS`` установленного ``lightrag-hku``
(без шапки-комментария в начале файла: иначе Jinja добавляет лишние переводы строк
в ``render_prompt`` и overlay расходится с библиотекой). При bump версии —
переснять строки из ``site-packages/lightrag/prompt.py`` в соответствующие ``.j2``.

Идея (см. план «Шаблоны LLM и LightRAG», блок B): по умолчанию подменяем
все известные нам prompt-ключи библиотеки `lightrag-hku` на наши
шаблоны-копии (для версии источника см. шапки соответствующих ``.j2``).
Это даёт пользователю возможность редактировать промпты лишь в одном
месте (``$THRELIUM_HOME/prompts/lightrag/``), не правя код.

Контракт:

* По умолчанию overlay включён. Отключить — ``THRELIUM_LIGHTRAG_PROMPTS_OVERLAY=0``
  (или ``false``/``no``/``off``); в этом случае работает чистая копия PROMPTS
  из библиотеки.
* Набор ключей — :class:`~threlium.types.lightrag_prompt_library_key.LightragPromptLibraryKey`
  (единственный StrEnum); путь шаблона — :meth:`LightragPromptLibraryKey.prompt_path`
  → :class:`~threlium.types.prompt_path.PromptPath`. Множество wire-имён для
  библиотеки экспортируется как :data:`KNOWN_OVERRIDABLE_KEYS`. Каждый из них
  **должен** присутствовать в текущей ``lightrag.prompt.PROMPTS``; иначе
  :func:`install_overlay` бросает ``RuntimeError`` (строгая политика без частичного overlay).
* Тип значения сохраняется: для list-typed ключей (``entity_extraction_examples``,
  ``keywords_extraction_examples``) рендер шаблона оборачивается в список
  из одного элемента — внутри lightrag они склеиваются через ``"\\n".join(...)``,
  поведение эквивалентно одному «толстому» примеру.
* Вызывать ровно один раз перед инстанциированием :class:`lightrag.LightRAG`
  (см. :func:`threlium.runners.lightrag._build_rag`). Повторный вызов
  безопасен, но смысла не имеет.
"""
from __future__ import annotations

from lightrag import prompt as lightrag_prompt

from threlium.logutil import logger
from threlium.prompts import render_prompt
from threlium.settings import ThreliumSettings
from threlium.types import LightragPromptLibraryKey

log = logger.bind(stage="lightrag")

KNOWN_OVERRIDABLE_KEYS: frozenset[str] = frozenset(k.value for k in LightragPromptLibraryKey)


def _overlay_enabled(settings: ThreliumSettings) -> bool:
    return settings.lightrag.prompts_overlay


def install_overlay(settings: ThreliumSettings) -> None:
    """Подменить ``lightrag.prompt.PROMPTS[k]`` нашими шаблонами для известных ``k``.

    Безопасна при многократном вызове. Если библиотека `lightrag-hku`
    не установлена — RuntimeError (как и в остальном lightrag-runtime).
    """
    if not _overlay_enabled(settings):
        log.info("prompts_overlay_disabled")
        return

    actual = lightrag_prompt.PROMPTS
    missing = sorted(k for k in KNOWN_OVERRIDABLE_KEYS if k not in actual)
    if missing:
        raise RuntimeError(
            "lightrag PROMPTS overlay: library missing keys required by overlay: "
            + ", ".join(missing)
        )
    applied = 0
    for lib_key in sorted(LightragPromptLibraryKey, key=lambda k: k.value):
        path = lib_key.prompt_path()
        rendered = render_prompt(path)
        if isinstance(actual[lib_key.value], list):
            actual[lib_key.value] = [rendered]
        else:
            actual[lib_key.value] = rendered
        applied += 1
    log.info("prompts_overlay_applied", applied=applied, total=len(KNOWN_OVERRIDABLE_KEYS))


__all__ = ["KNOWN_OVERRIDABLE_KEYS", "install_overlay"]
