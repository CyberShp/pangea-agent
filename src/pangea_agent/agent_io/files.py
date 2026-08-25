from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 格式错误：{path} ({exc})") from exc


def write_json(path: Path, value: Any) -> None:
    """Atomically replace a JSON artifact so an interrupted write is never accepted."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
