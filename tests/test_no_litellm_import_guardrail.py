"""Guardrail: импорт продакшн-модулей Threlium НЕ должен тянуть ``litellm``.

После перехода на собственный OpenAI-совместимый клиент (:mod:`threlium.openai_compatible_client`
+ :mod:`threlium.llm_wire`) ни ``threlium.types``, ни путь движка, ни мост ``isomorph`` не зависят
от тяжёлого ``litellm`` (~1.65s импорт). Проверяем в ОТДЕЛЬНОМ интерпретаторе (pytest сам по себе
мог бы затянуть litellm через сторонние плагины), чтобы зафиксировать чистоту графа импорта.
"""
from __future__ import annotations

import subprocess
import sys


def test_threlium_production_imports_pull_no_litellm() -> None:
    # Модули, покрывающие всю LLM-границу Threlium: chat/embeddings/rerank-клиент, lightrag-адаптеры
    # и isomorph-энкодеры. Все они после миграции не должны тянуть litellm.
    code = (
        "import sys\n"
        "import threlium.types\n"
        "import threlium.litellm_client\n"
        "import threlium.openai_compatible_client\n"
        "import threlium.llm_wire\n"
        "import threlium.runners.lightrag._adapters\n"
        "import threlium.bridges.isomorph.encoders\n"
        "leaked = sorted(m for m in sys.modules if m == 'litellm' or m.startswith('litellm.'))\n"
        "assert not leaked, f'litellm leaked into import graph: {leaked}'\n"
        "print('no-litellm OK')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    assert "no-litellm OK" in r.stdout, r.stdout
