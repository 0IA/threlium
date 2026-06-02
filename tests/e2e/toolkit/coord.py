"""Xdist / shared compose coordination files."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .constants import REPO_ROOT

def e2e_compose_coord_dir() -> Path:
    """Каталог координаторов shared compose: стабилен между отдельными вызовами ``pytest`` (тот же checkout)."""
    workspace_hash = hashlib.sha256(str(REPO_ROOT.resolve()).encode()).hexdigest()[:12]
    d = Path(tempfile.gettempdir()) / f"threlium_e2e_compose_coord_{workspace_hash}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def e2e_compose_coord_paths() -> tuple[Path, Path, Path]:
    """``(lock_path, ready_flag_path, runtime_json_path)`` для лидера/фолловеров ``compose_stack``."""
    d = e2e_compose_coord_dir()
    return (
        d / "e2e_compose_setup.lock",
        d / "e2e_compose_ready.flag",
        d / "e2e_shared_runtime.json",
    )


def e2e_controller_hint_path() -> Path:
    """Путь подсказки контроллера pytest (``sessionfinish``): от ``cwd``, как раньше в ``conftest``."""
    workspace_hash = hashlib.sha256(str(Path.cwd()).encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"threlium_e2e_{workspace_hash}.json"


def e2e_controller_hint_write(
    project_name: str,
    *,
    runtime_json_path: Path | None = None,
) -> None:
    hint = e2e_controller_hint_path()
    hint.write_text(
        json.dumps({
            "project_name": project_name,
            "pid": os.getpid(),
            "cwd": str(Path.cwd()),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "runtime_json": str(runtime_json_path) if runtime_json_path else None,
        })
    )


def e2e_controller_hint_read() -> str | None:
    try:
        hint = e2e_controller_hint_path()
        if hint.is_file():
            data = json.loads(hint.read_text())
            return data.get("project_name") or None
    except (OSError, ValueError, KeyError):
        pass
    return None


def e2e_controller_hint_cleanup() -> None:
    try:
        e2e_controller_hint_path().unlink(missing_ok=True)
    except OSError:
        pass
