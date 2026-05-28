"""Фильтр IRT-цепочки для enrich: изоляция по границам субагентов.

Универсальный алгоритм — enrich не знает, субагент он или корень; всё
определяется единственным счётчиком ``skip_counter``.

Yield'ятся только сообщения **своего** уровня:

* ``subagent_end`` при skip_counter == 0 → yield (результат прямого ребёнка),
  затем skip_counter += 1 (входим в зону ребёнка).
* ``subagent_end`` при skip_counter > 0 → пропуск (результат внука),
  skip_counter += 1.
* ``subagent_intent`` при skip_counter > 1 → пропуск (задача внуку),
  skip_counter -= 1.
* ``subagent_intent`` при skip_counter == 1 → skip_counter -= 1, yield
  (наша постановка задачи прямому ребёнку, выходим из его зоны).
* ``subagent_intent`` при skip_counter == 0 → yield + STOP (граница:
  intent, породивший текущего агента).
* Обычное сообщение при skip_counter == 0 → yield.
* Обычное сообщение при skip_counter > 0 → пропуск.
"""
from __future__ import annotations

from collections.abc import Iterator

from threlium.irt_chain import IrtAncestorSnapshot, iter_in_reply_to_ancestors_from_inner_id
from threlium.types import FsmStage, NotmuchMessageIdInner


def iter_irt_ancestors_filtered(
    leaf_inner: NotmuchMessageIdInner,
) -> Iterator[IrtAncestorSnapshot]:
    """IRT-обход вверх с изоляцией по границам субагентов (skip_counter).

    Yield'ятся только снимки собственного уровня: результаты прямых
    детей и постановки задач им; внуки/правнуки полностью изолированы.
    """
    skip_counter = 0

    for snap in iter_in_reply_to_ancestors_from_inner_id(leaf_inner):
        is_intent = snap.is_sent_from_fsm_stage(FsmStage.SUBAGENT_INTENT)
        is_end = snap.is_sent_from_fsm_stage(FsmStage.SUBAGENT_END)

        if is_end:
            if skip_counter == 0:
                yield snap
            skip_counter += 1
            continue

        if is_intent:
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
