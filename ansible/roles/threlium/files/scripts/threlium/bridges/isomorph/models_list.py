"""``GET /v1/models`` — каталог моделей из ``settings.litellm`` (опционально; Cline не префлайтит).

Cline читает только ``data[].id``. ``model`` клиента всё равно эхо-возвращается в ответе как есть.
"""
from __future__ import annotations

from threlium.settings import ThreliumSettings


def models_list_payload(settings: ThreliumSettings) -> dict[str, object]:
    seen: list[str] = []
    for ep in settings.litellm.llm_endpoints:
        mid = (ep.model or "").strip()
        if ep.enabled and mid and mid not in seen:
            seen.append(mid)
    if not seen:
        seen = ["threlium"]
    return {
        "object": "list",
        "data": [
            {"id": mid, "object": "model", "created": 0, "owned_by": "threlium"}
            for mid in seen
        ],
    }
