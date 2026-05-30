"""Фильтр IRT-цепочки: единая header-free изоляция по границам фрейма (и хода).

Универсальный алгоритм — потребитель не знает, субагент он или корень; всё
определяется единственным счётчиком ``skip_counter`` (баланс маркеров
``subagent_intent``/``subagent_end`` через :meth:`IrtAncestorSnapshot.subagent_marker`).

**Два барьера** (не путать):

* Барьер ФРЕЙМА — первый незакрытый ``subagent_intent`` (skip_counter == 0):
  начало текущего уровня субагента. task-ledger живёт per-frame и НЕ
  сбрасывается ходами пользователя внутри фрейма.
* Барьер ХОДА — ``tag:route`` (корень текущего user-сообщения), включается
  опцией ``stop_at_route``. response-буфер/observation живут per-frame И
  per-turn, поэтому при сборе буфера обход обрывается на корне хода.

Yield'ятся только сообщения **своего** уровня:

* ``subagent_end`` при skip_counter == 0 → yield (результат прямого ребёнка),
  затем skip_counter += 1 (входим в зону ребёнка).
* ``subagent_end`` при skip_counter > 0 → пропуск (результат внука),
  skip_counter += 1.
* ``subagent_intent`` при skip_counter > 1 → пропуск (задача внуку),
  skip_counter -= 1.
* ``subagent_intent`` при skip_counter == 1 → skip_counter -= 1, yield
  (наша постановка задачи прямому ребёнку, выходим из его зоны).
* ``subagent_intent`` при skip_counter == 0 → yield + STOP (граница фрейма:
  intent, породивший текущего агента).
* Обычное сообщение при skip_counter == 0 → yield.
* Обычное сообщение при skip_counter > 0 → пропуск.
"""
from __future__ import annotations

from collections.abc import Iterator

from threlium.irt_chain import (
    IrtAncestorSnapshot,
    IrtSubagentMarker,
    iter_in_reply_to_ancestors_from_inner_id,
)
from threlium.types import NotmuchMessageIdInner, NotmuchTag


def iter_irt_ancestors_filtered(
    leaf_inner: NotmuchMessageIdInner,
    *,
    stop_at_route: bool = False,
) -> Iterator[IrtAncestorSnapshot]:
    """IRT-обход вверх с изоляцией по границам субагентов (skip_counter).

    Yield'ятся только снимки собственного уровня: результаты прямых
    детей и постановки задач им; внуки/правнуки полностью изолированы.

    ``stop_at_route`` — добавляет барьер ХОДА: обход обрывается (эксклюзивно,
    без yield) на снимке с ``tag:route`` — корне текущего user-сообщения. Для
    буфера ответа/observation, которые не должны переживать ход пользователя.
    """
    skip_counter = 0

    for snap in iter_in_reply_to_ancestors_from_inner_id(leaf_inner):
        if stop_at_route and NotmuchTag.ROUTE.value in snap.tags:
            return

        marker = snap.subagent_marker()

        if marker is IrtSubagentMarker.SUBAGENT_END:
            if skip_counter == 0:
                yield snap
            skip_counter += 1
            continue

        if marker is IrtSubagentMarker.SUBAGENT_INTENT:
            if skip_counter > 1:
                skip_counter -= 1
                continue
            if skip_counter == 1:
                skip_counter -= 1
                yield snap
                continue
            yield snap
            return

        if skip_counter == 0:
            yield snap
