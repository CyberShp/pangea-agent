from __future__ import annotations

RESOURCE_KEYWORDS = (
    "open",
    "close",
    "pcall",
    "xpcall",
    "coroutine",
    "socket",
    "timer",
    "lock",
    "unlock",
    "timeout",
    "retry",
)


def extract_lua_resource_signals(lines: list[str]) -> list[dict]:
    signals = []
    for line_number, line in enumerate(lines, 1):
        lowered = line.lower()
        hits = [keyword for keyword in RESOURCE_KEYWORDS if keyword in lowered]
        if hits:
            signals.append(
                {
                    "line": line_number,
                    "keywords": hits,
                    "text": line.strip()[:200],
                }
            )
    return signals
