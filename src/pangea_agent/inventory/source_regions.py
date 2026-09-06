"""Build stable, lossless source regions from the frozen inventory.

Regions are navigation coordinates only.  They are intentionally independent
of analysis quality: a parser error produces a raw region covering the
unparsed text instead of silently removing that text from the worker's view.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from pangea_agent.models.source_first import SourceFileIndex, SourceRegion


def _region_id(
    repo_id: str,
    path: str,
    kind: str,
    line_start: int,
    line_end: int,
    symbol: str | None,
) -> str:
    identity = "|".join(
        (
            repo_id,
            path.replace("\\", "/"),
            kind,
            str(line_start),
            str(line_end),
            symbol or "",
        )
    )
    return f"r-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def _bounded_range(item: dict, line_count: int) -> tuple[int, int] | None:
    if line_count <= 0:
        return None
    try:
        start = int(item.get("line", 0))
        end = int(item.get("end_line", start))
    except (TypeError, ValueError):
        return None
    start = max(1, min(start, line_count))
    end = max(start, min(end, line_count))
    return start, end


def _add(
    regions: list[SourceRegion],
    *,
    repo_id: str,
    path: str,
    kind: str,
    item: dict,
    line_count: int,
    parse_complete: bool,
    parsing_note: str | None = None,
) -> None:
    bounded = _bounded_range(item, line_count)
    if bounded is None:
        return
    line_start, line_end = bounded
    symbol = item.get("symbol") or item.get("name") or item.get("condition")
    symbol = str(symbol) if symbol else None
    region = SourceRegion(
        region_id=_region_id(repo_id, path, kind, line_start, line_end, symbol),
        repo_id=repo_id,
        path=path,
        kind=kind if kind in {"function", "type", "global", "macro", "branch", "raw"} else "raw",
        line_start=line_start,
        line_end=line_end,
        symbol=symbol,
        parser=str(item.get("parser")) if item.get("parser") else None,
        parse_complete=parse_complete,
        parsing_note=parsing_note,
    )
    if not any(existing.region_id == region.region_id for existing in regions):
        regions.append(region)


def _raw_ranges(line_count: int, covered: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Return every line not covered by a structural region."""

    marks = [False] * (line_count + 1)
    for start, end in covered:
        for line in range(max(1, start), min(line_count, end) + 1):
            marks[line] = True
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for line in range(1, line_count + 1):
        if not marks[line] and start is None:
            start = line
        if marks[line] and start is not None:
            ranges.append((start, line - 1))
            start = None
    if start is not None:
        ranges.append((start, line_count))
    return ranges


def build_file_regions(file_record: dict) -> SourceFileIndex:
    repo_id = str(file_record["repo_id"])
    path = str(file_record["path"]).replace("\\", "/")
    line_count = max(0, int(file_record.get("line_count", 0)))
    parse_complete = bool(file_record.get("parse_complete", False))
    parsing_note = None
    if not parse_complete:
        parsing_note = str(
            file_record.get("fallback_analysis")
            or "结构解析不完整，保留原文区域供 Agent 阅读"
        )

    regions: list[SourceRegion] = []
    structural_ranges: list[tuple[int, int]] = []
    collections = (
        ("function", file_record.get("functions", [])),
        ("type", file_record.get("types", [])),
        ("macro", file_record.get("preprocessor", [])),
        ("branch", file_record.get("branches", [])),
    )
    for kind, items in collections:
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            bounded = _bounded_range(item, line_count)
            if bounded is not None:
                structural_ranges.append(bounded)
            _add(
                regions,
                repo_id=repo_id,
                path=path,
                kind=kind,
                item=item,
                line_count=line_count,
                parse_complete=parse_complete,
                parsing_note=parsing_note,
            )

    # A parser may not expose declarations, comments, blank lines, or an
    # entire malformed file.  Raw regions make those bytes addressable.
    for start, end in _raw_ranges(line_count, structural_ranges):
        _add(
            regions,
            repo_id=repo_id,
            path=path,
            kind="raw",
            item={"line": start, "end_line": end},
            line_count=line_count,
            parse_complete=parse_complete,
            parsing_note=parsing_note,
        )

    regions.sort(key=lambda item: (item.line_start, item.line_end, item.kind, item.region_id))
    return SourceFileIndex(
        repo_id=repo_id,
        path=path,
        line_count=line_count,
        regions=regions,
        parse_complete=parse_complete,
        region_count=len(regions),
    )


def build_source_index(inventory: dict) -> dict:
    """Return a serialisable, complete region index for an inventory."""

    files = [
        build_file_regions(item)
        for item in inventory.get("files", [])
        if isinstance(item, dict) and item.get("repo_id") and item.get("path")
    ]
    files.sort(key=lambda item: (item.repo_id, item.path))
    return {
        "format_version": "pangea-source-index-v1",
        "files": [item.model_dump(mode="json") for item in files],
        "file_count": len(files),
        "region_count": sum(len(item.regions) for item in files),
        "parse_failures": inventory.get("parse_failures", []),
    }
