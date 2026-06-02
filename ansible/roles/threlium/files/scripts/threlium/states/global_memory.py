#!/usr/bin/env python3
"""global_memory@localhost → enrich_fast@localhost (docs/MEMORY_TABLE.md §2).

Тонкий re-export общего durable-memory обработчика (``_memory_write``); вся
логика — там. Имя ``main`` сохранено для ``states.registry``.
"""
from threlium.states._memory_write import emit_memory_note_to_enrich_fast as main

__all__ = ["main"]
