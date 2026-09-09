from __future__ import annotations

import json
from typing import Any


API_VERSION = "1.0"

# Keep the JSON wire format writable through Windows pipes using legacy code pages.
# JSON consumers recover the original Unicode strings without data loss.

def print_success(result: Any) -> None:
    print(json.dumps(
        {"api_version": API_VERSION, "ok": True, "result": result},
        ensure_ascii=True,
    ))


def print_error(exc: Exception) -> None:
    print(json.dumps(
        {
            "api_version": API_VERSION,
            "ok": False,
            "error": {"code": exc.__class__.__name__, "message": str(exc)},
        },
        ensure_ascii=True,
    ))
