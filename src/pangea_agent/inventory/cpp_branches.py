from __future__ import annotations

import re


def extract_branches(lines: list[str]) -> list[dict]:
    points = []
    for idx, raw in enumerate(lines, 1):
        text = raw.split("//", 1)[0].strip()
        if not text or text.startswith("#"):
            continue
        if re.search(r"\belse\s+if\s*\(", text):
            points.append({"line": idx, "kind": "else_if"})
        elif re.search(r"\bif\s*\(", text):
            points.append({"line": idx, "kind": "if"})
        if re.search(r"\belse\b", text) and "else if" not in text:
            points.append({"line": idx, "kind": "else"})
        if re.search(r"\bcase\b[^:]*:", text):
            points.append({"line": idx, "kind": "case"})
        if re.search(r"\bdefault\s*:", text):
            points.append({"line": idx, "kind": "default"})
    return points
