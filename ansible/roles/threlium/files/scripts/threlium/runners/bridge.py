import sys
from email.message import EmailMessage


def main() -> None:
    from threlium.logutil import logger, setup_logging, shutdown_logging
    from threlium.settings import load_settings

    settings = load_settings()
    setup_logging(settings.log_level)

    from threlium.bridges.registry import BRIDGE_RUNNERS
    from threlium.delivery import fdm_bytes_from_message, run_fdm
    from threlium.systemd_notify import notify_status
    from threlium.types.bridge_ingress_channel import BridgeIngressChannel
    from threlium.types.systemd_status import SystemdStatusBody

    log = logger.bind(stage="bridge_runner")

    try:
        if len(sys.argv) < 2:
            log.error("missing_channel_arg")
            sys.exit(1)
        channel = sys.argv[1].strip()

        try:
            ch = BridgeIngressChannel(channel)
        except ValueError as exc:
            known = ", ".join(m.value for m in BridgeIngressChannel)
            log.error("unknown_channel", channel=channel, known=known, exc_info=exc)
            sys.exit(1)

        run_bridge = BRIDGE_RUNNERS[ch]
        notify_status(SystemdStatusBody.bridge_channel_starting(ch))

        def deliver(msg: EmailMessage) -> None:
            run_fdm(fdm_bytes_from_message(msg))

        run_bridge(deliver, settings=settings)
    finally:
        shutdown_logging()


if __name__ == "__main__":
    main()
