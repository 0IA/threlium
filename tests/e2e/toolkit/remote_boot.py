"""Remote probe boot snippets for SUT heredoc."""
from __future__ import annotations

# Heredoc на SUT: ``StreamHandler(sys.stdout)`` + ``%(message)s`` — по строке на ``.info`` (протокол парсинга stdout).
REMOTE_PROBE_LOGGER_BOOT = (
    "import logging, sys\n"
    "_probe_out = logging.getLogger('threlium.e2e.remote')\n"
    "if not _probe_out.handlers:\n"
    "    _h = logging.StreamHandler(sys.stdout)\n"
    "    _h.setFormatter(logging.Formatter('%(message)s'))\n"
    "    _probe_out.addHandler(_h)\n"
    "    _probe_out.setLevel(logging.INFO)\n"
    "    _probe_out.propagate = False\n"
)
# SUT heredoc: system ``python3`` не видит editable ``threlium`` (только agent/.venv).
REMOTE_RFC822_PARSER_BOOT = (
    "from email import policy\n"
    "from email.parser import BytesParser\n"
    "def parse_rfc822(data):\n"
    "    if isinstance(data, str):\n"
    "        data = data.encode('utf-8', errors='replace')\n"
    "    return BytesParser(policy=policy.default).parsebytes(data)\n"
)
