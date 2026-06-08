"""E2E-ONLY: типизированные директивы в теле сообщения (обобщение ``E2E_MID:``, ``docs/E2E.md`` §2.3).

Тест в e2e-режиме инжектит в тело (которое он же контролирует — стаб/инъекция) директиву
``E2E_<KEY>:<value>`` и продукт в **e2e-режиме** (флаг ``settings.e2e.litellm_route_correlation``) читает её
ВМЕСТО глобального ``threlium.yaml``-конфига + рестарт engine. Это позволяет тесту параметризовать прогон
**per-message** (без рестарта общего стека → совместимо с ``-n N``), той же идеей, что
:func:`~threlium.bridges.isomorph.snowflake_mid.extract_e2e_explicit_mid` для thread-root MID.

**В ПРОДЕ не используется** (вызывается только за флагом e2e у вызывающей стадии) — пользователь не может
повлиять на бюджет/конфиг через содержимое своего письма. Извлечённый токен удаляется из тела (как
``extract_e2e_explicit_mid``), чтобы не утекать в downstream-промпты.
"""
from __future__ import annotations

import re


def _directive_re(key: str) -> re.Pattern[str]:
    return re.compile(r"E2E_" + re.escape(key) + r":(-?\d+)")


def extract_e2e_int_directive(body: str, key: str) -> tuple[int | None, str]:
    """Вынуть целочисленную директиву ``E2E_<KEY>:<int>`` из тела → ``(value, тело-без-токена)``.

    Нет токена → ``(None, body)``. Берётся ПЕРВОЕ совпадение. Гейтинг по e2e-режиму — на стороне вызывающего.
    """
    m = _directive_re(key).search(body or "")
    if m is None:
        return None, body
    value = int(m.group(1))
    cleaned = (body[: m.start()] + body[m.end():]).strip()
    return value, cleaned
