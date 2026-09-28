from __future__ import annotations

import json
import sys
import traceback
from typing import Any


API_VERSION = "1.0"


def _print_response(envelope: dict[str, Any], *, readable: bool) -> None:
    text = None
    if readable:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        result = envelope.get("result")
        if isinstance(result, dict) and isinstance(result.get("text"), str):
            text = result["text"]
            envelope = {**envelope, "result": {key: value for key, value in result.items() if key != "text"}}
    print(json.dumps(
        envelope,
        # Workers may decode Windows pipes as UTF-8 while Python uses a local
        # code page. ASCII JSON preserves the exact Unicode payload in both.
        ensure_ascii=not readable,
        indent=2 if readable else None,
    ))
    if text is not None:
        print(text)


def print_success(result: Any, *, readable: bool = False) -> None:
    _print_response({"api_version": API_VERSION, "ok": True, "result": result}, readable=readable)


def print_error(exc: Exception, *, readable: bool = False) -> None:
    # Preserve the failing operation across the CLI boundary. Do not include
    # frame locals or source text (which may contain private input).
    frames = traceback.extract_tb(exc.__traceback__)
    detail = "\n".join(
        f"{frame.filename}:{frame.lineno} in {frame.name}"
        for frame in frames[-12:]
    )
    _print_response(
        {
            "api_version": API_VERSION,
            "ok": False,
            "error": {"code": exc.__class__.__name__, "message": str(exc),
                      **({"detail": detail} if detail else {})},
        },
        readable=readable,
    )
