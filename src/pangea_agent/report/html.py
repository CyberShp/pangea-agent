from __future__ import annotations

import html


def markdown_to_minimal_html(markdown: str) -> str:
    return "<html><body><pre>" + html.escape(markdown) + "</pre></body></html>"
