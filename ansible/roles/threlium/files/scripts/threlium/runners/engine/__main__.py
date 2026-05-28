"""``python -m threlium.runners.engine`` — долгоживущий демон."""

from threlium.logutil import setup_logging
from threlium.settings import load_settings

setup_logging(load_settings().log_level)

from threlium.runners.engine.server import main

if __name__ == "__main__":
    main()
