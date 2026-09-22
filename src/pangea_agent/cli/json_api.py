from __future__ import annotations

import json
import traceback
from typing import Any


API_VERSION = "1.0"


def print_success(result: Any) -> None:
    print(json.dumps(
        {"api_version": API_VERSION, "ok": True, "result": result},
        ensure_ascii=False,
    ))


def print_error(exc: Exception) -> None:
    # Preserve the failing operation across the CLI boundary. Do not include
    # frame locals or source text (which may contain private input).
    frames = traceback.extract_tb(exc.__traceback__)
    detail = "\n".join(
        f"{frame.filename}:{frame.lineno} in {frame.name}"
        for frame in frames[-12:]
    )
    print(json.dumps(
        {
            "api_version": API_VERSION,
            "ok": False,
            "error": {"code": exc.__class__.__name__, "message": str(exc),
                      **({"detail": detail} if detail else {})},
        },
        ensure_ascii=False,
    ))
