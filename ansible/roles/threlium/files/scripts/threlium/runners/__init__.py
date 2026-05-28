"""Оркестрационный слой Threlium FSM: движок + fdm-triggered dispatch.

После каждого терминирующего ``notmuch insert`` в пайпе ``fdm`` (``~/.fdm.conf``) тот же шелл
запускает ``threlium-dispatch.sh``, который поднимает
``threlium-work@<stage>:<thread_id>.service`` для каждого unread треда.

* :mod:`threlium.runners.engine` — долгоживущий процесс ``threlium-engine.service``
  (``python -m threlium.runners.engine``); на каждый инстанс ``threlium-work@``
  shell-submit передаёт задание по UNIX-сокету → :func:`~threlium.runners.engine.process_thread_message`.
  Хвосты подбирает ``threlium-sweep@%i.service`` после **успешного** exit submit
  (``OnSuccess=`` на ``threlium-work@``, см. ORCHESTRATION).
"""
