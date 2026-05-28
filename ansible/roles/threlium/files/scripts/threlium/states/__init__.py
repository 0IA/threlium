"""FSM-стадии Threlium. Каждый модуль — функция-состояние

``main(msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings) -> EmailMessage | None``.
Воркер (пакет ``threlium.runners.engine``) вызывает handler in-process.
"""
