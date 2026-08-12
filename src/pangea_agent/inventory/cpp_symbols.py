from __future__ import annotations

import re

_FUNCTION_RE = re.compile(r"^\s*(?:static\s+)?(?:inline\s+)?[A-Za-z_][\w\s\*]+\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{")


def extract_functions(lines: list[str]) -> list[dict]:
    results = []
    for idx, line in enumerate(lines, 1):
        match = _FUNCTION_RE.match(line)
        if match:
            results.append({"line": idx, "symbol": match.group(1)})
    return results
