"""Shared Python fragments embedded in SUT notmuch probe heredocs."""
from __future__ import annotations

# Included verbatim in remote ``python3 <<'PY'`` scripts on SUT.
PY_PATHS_HELPER = """
def _paths(raw: str) -> list[str]:
    try:
        payload = json.loads((raw or "").strip() or "[]")
    except json.JSONDecodeError:
        return []
    out: list[str] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, str):
                s = item.strip()
            elif isinstance(item, dict):
                s = str(item.get("path") or item.get("file") or item.get("filename") or "").strip()
            else:
                s = ""
            if s:
                out.append(s)
    return out
""".strip()


PY_FIRST_THREAD_HELPER = """
def _first_thread(raw: str) -> str:
    try:
        payload = json.loads((raw or "").strip() or "[]")
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, list) or not payload:
        return ""
    first = payload[0]
    if isinstance(first, str):
        tid = first.strip()
    elif isinstance(first, dict):
        tid = str(first.get("thread") or first.get("thread_id") or first.get("threadid") or "").strip()
    else:
        tid = ""
    if not tid:
        return ""
    return tid if tid.startswith("thread:") else f"thread:{tid}"
""".strip()


def remote_notmuch_thread_helpers_py() -> str:
    """``_paths`` + ``_first_thread`` for inclusion in SUT probe scripts."""
    return f"{PY_PATHS_HELPER}\n\n{PY_FIRST_THREAD_HELPER}"
