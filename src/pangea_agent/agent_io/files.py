from __future__ import annotations

from pangea_agent.fs_paths import long_path

from collections.abc import Sequence
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

# Windows MoveFileExW (os.replace) fails with ERROR_ACCESS_DENIED (5) or
# ERROR_SHARING_VIOLATION (32) whenever another process (UI polling, sibling
# workers, or AV/EDR scanners) briefly holds the target file open.
_IS_WINDOWS = os.name == "nt"
_WINDOWS_TRANSIENT_WINERRORS = frozenset({5, 32, 33})
_WINDOWS_REPLACE_RETRY_DELAYS = (
    0.05, 0.1, 0.2, 0.3, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5,
)
_WINDOWS_READ_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.5, 0.5, 0.5)


def _is_windows_transient_lock(exc: OSError) -> bool:
    if not _IS_WINDOWS:
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        return winerror in _WINDOWS_TRANSIENT_WINERRORS
    return isinstance(exc, PermissionError)


def atomic_replace(
    source: str | os.PathLike[str],
    target: str | os.PathLike[str],
    *,
    delays: Sequence[float] = _WINDOWS_REPLACE_RETRY_DELAYS,
) -> None:
    """Atomically replace target with source, retrying transient Windows file locks."""

    src = long_path(source)
    dst = long_path(target)
    for delay in (*delays, None):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            if delay is None or not _is_windows_transient_lock(exc):
                raise
            time.sleep(delay)


def read_json(path: Path) -> Any:
    target = long_path(path)
    content = ""
    for delay in (*_WINDOWS_READ_RETRY_DELAYS, None):
        try:
            content = target.read_text(encoding="utf-8")
            break
        except OSError as exc:
            if delay is None or not _is_windows_transient_lock(exc):
                raise
            time.sleep(delay)
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 格式错误：{path} ({exc})") from exc


def write_json(path: Path, value: Any) -> None:
    """Atomically replace a JSON artifact so an interrupted write is never accepted."""

    long_path(path.parent).mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=long_path(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
        atomic_replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
