from __future__ import annotations

RESOURCE_KEYWORDS = ("alloc", "free", "malloc", "calloc", "kzalloc", "ref", "put", "get", "queue", "lock", "unlock", "timeout", "retry")


def extract_resource_signals(lines: list[str]) -> list[dict]:
    signals = []
    for idx, line in enumerate(lines, 1):
        lowered = line.lower()
        hits = [word for word in RESOURCE_KEYWORDS if word in lowered]
        if hits:
            signals.append({"line": idx, "keywords": hits, "text": line.strip()[:200]})
    return signals
