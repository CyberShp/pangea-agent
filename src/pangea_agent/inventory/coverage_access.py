"""Bound navigation over the current task's frozen Coverage inputs.

This is an index/projection, not a second coverage matcher or a semantic filter.
No assets are queried and neither frozen inputs nor the task are rewritten.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from pangea_agent.graph.result_store import compact_items_page
from pangea_agent.inventory.source_access import SourceAccessError, resolve_binding


def _path(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("\\", "/")
    return normalized.rstrip("/") or normalized


def _locations(record: Any) -> list[tuple[str | None, str | None]]:
    if not isinstance(record, dict):
        return [(None, None)]
    matches = record.get("matches")
    locations = []
    for match in matches if isinstance(matches, list) else []:
        if isinstance(match, dict) and _path(match.get("path")):
            repo = match.get("repo_id")
            locations.append((repo if isinstance(repo, str) else None, _path(match["path"])))
    if not locations:
        repo = record.get("repo_id")
        locations.append((repo if isinstance(repo, str) else None,
                          _path(record.get("path")) or _path(record.get("file_path"))))
    return list(dict.fromkeys(locations))


def _inside(location: tuple, repo_id: str | None, path: str | None) -> bool:
    repo, filename = location
    return (repo_id is None or repo == repo_id) and (
        path is None or filename is not None and (
            filename == path or filename.startswith(path if path.endswith("/") else path + "/"))
    )


def _summary(entries: list[dict], repo_id: str | None, path: str | None, depth: int) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for entry in entries:
        record = entry["record"]
        for repo, filename in entry["locations"]:
            if not _inside((repo, filename), repo_id, path):
                continue
            if filename is None:
                key = (repo, None, "unlocated")
            elif path == filename:
                key = (repo, filename, "file")
            else:
                prefix = (path if path.endswith("/") else path + "/") if path else ""
                relative = filename[len(prefix):] if prefix else filename
                # Preserve absolute/UNC roots so the returned group remains a
                # usable filter; a leading slash must not become an empty group.
                root = "//" if relative.startswith("//") else "/" if relative.startswith("/") else ""
                parts = relative[len(root):].split("/")
                key = (repo, prefix + root + "/".join(parts[:depth]),
                       "directory" if len(parts) > depth else "file")
            group = groups.setdefault(key, {"indices": set(), "locations": 0, "types": Counter(),
                                            "states": Counter(), "sections": Counter()})
            group["locations"] += 1
            if entry["ordinal"] not in group["indices"]:
                group["indices"].add(entry["ordinal"])
                for source_key, counter_key in (("coverage_type", "types"), ("coverage_state", "states")):
                    value = record.get(source_key) if isinstance(record, dict) else None
                    group[counter_key][value if isinstance(value, str) else "unspecified"] += 1
                group["sections"][entry["section"]] += 1
    return [{"item_type": "coverage_group", "value": {
                "repo_id": key[0], "path": key[1], "kind": key[2],
                "distinct_records": len(value["indices"]), "location_count": value["locations"],
                "coverage_types": dict(sorted(value["types"].items())),
                "coverage_states": dict(sorted(value["states"].items())),
                "sections": dict(sorted(value["sections"].items())),
            }} for key, value in sorted(groups.items(), key=lambda item: tuple(str(v or "") for v in item[0]))]


def coverage_read(
    data_root: str, run_id: str, action_id: str, task_id: str, *,
    input_id: str = "coverage_gaps", view: str = "summary",
    repo_id: str | None = None, path: str | None = None, depth: int = 1,
    gap_ids: list[str] | None = None, page_token: str | None = None,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    """Read a bounded summary, ID index or original frozen Coverage records.

    ``summary`` groups by the next ``depth`` path segments under ``path``;
    ``index`` projects real coverage_id/type/state/count/side/source fields and
    frozen candidate locations; ``records`` returns unchanged records. For
    example, summary(path='src/tcp', depth=1), followed by
    index(path='src/tcp/auth.c'), supplies IDs for a unit without reading raw
    attachments. Filtering uses exact path or directory-segment prefix, never
    basename/suffix guessing. A record with several candidate matches occurs
    once in records/index but may contribute to several summary groups.

    Responses use the existing items/item_fragment/next_page_token protocol.
    Oversized items are losslessly fragmented; concatenate a fragment's text
    in char_start order before JSON parsing. Summary counts are descriptive,
    not completion obligations. An empty result never means 100% coverage.
    """
    binding, run_dir, _, task = resolve_binding(data_root, run_id, action_id, task_id)
    if input_id not in {"coverage_gaps", "coverage_match_summary"}:
        raise SourceAccessError("coverage-read input_id 必须为 coverage_gaps 或 coverage_match_summary")
    if view not in {"summary", "index", "records"}:
        raise SourceAccessError("coverage-read view 必须为 summary、index 或 records")
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 64:
        raise SourceAccessError("coverage-read depth 必须在 1 到 64 之间")
    if repo_id is not None and (not isinstance(repo_id, str) or not repo_id):
        raise SourceAccessError("coverage-read repo_id 必须为非空字符串")
    if path is not None and (not isinstance(path, str) or not path):
        raise SourceAccessError("coverage-read path 必须为非空字符串")
    if gap_ids is not None and (not isinstance(gap_ids, list) or any(not isinstance(i, str) or not i for i in gap_ids)):
        raise SourceAccessError("coverage-read gap_ids 必须为真实 coverage_id 的字符串数组")
    path = None if path == "." else _path(path)
    declared = [item for item in task.get("inputs", [])
                if isinstance(item, dict) and item.get("input_id") == input_id and item.get("path")]
    if len(declared) != 1:
        raise SourceAccessError("Coverage 输入未唯一授权给当前 task")
    input_path = Path(declared[0]["path"]).resolve()
    try:
        input_path.relative_to(run_dir)
    except ValueError as exc:
        raise SourceAccessError("Coverage 输入路径越出当前 Run 数据边界") from exc
    try:
        raw = input_path.read_bytes()
        content = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceAccessError("冻结 Coverage 输入不可读取") from exc
    if input_id == "coverage_gaps":
        if not isinstance(content, list):
            raise SourceAccessError("冻结 coverage_gaps 必须为数组")
        sections, header = {"gaps": content}, None
    else:
        if not isinstance(content, dict):
            raise SourceAccessError("冻结 coverage_match_summary 必须为对象")
        sections = {key: content.get(key, []) for key in ("matched", "unmatched", "ambiguous")}
        if any(not isinstance(items, list) for items in sections.values()):
            raise SourceAccessError("冻结 Coverage 匹配明细必须为数组")
        header = {key: value for key, value in content.items() if key not in sections}
    entries = [{"ordinal": index, "section": section, "record": record, "locations": _locations(record)}
               for index, (section, record) in enumerate(
                   (section, record) for section, records in sections.items() for record in records)]
    identifiers = None if gap_ids is None else set(gap_ids)
    selected = [entry for entry in entries
                if (identifiers is None or isinstance(entry["record"], dict)
                    and isinstance(entry["record"].get("coverage_id"), str)
                    and entry["record"]["coverage_id"] in identifiers)
                and any(_inside(location, repo_id, path) for location in entry["locations"])]
    items = []
    if view == "summary":
        items.extend(_summary(selected, repo_id, path, depth))
    else:
        index_fields = ("coverage_id", "asset_id", "coverage_type", "coverage_state", "source",
                        "repo_id", "path", "file_path", "function", "line", "branch_id", "block", "branch",
                        "count", "true_count", "false_count", "matches")
        for entry in selected:
            record = entry["record"]
            value = ({key: record[key] for key in index_fields if key in record}
                     if view == "index" and isinstance(record, dict) else record)
            item = {"item_type": "coverage_index" if view == "index" else "coverage_record",
                    "section": entry["section"], "record_index": entry["ordinal"], "value": value}
            if view == "index":
                item["locations"] = [{"repo_id": repo, "path": filename} for repo, filename in entry["locations"]]
            items.append(item)
    if header is not None:
        items.append({"item_type": "coverage_input_metadata", "value": header})
    digest = hashlib.sha256(raw).hexdigest()
    metadata = {"format_version": "pangea-coverage-read-v1", "binding": binding.model_dump(mode="json"),
                "input_id": input_id, "input_sha256": digest, "view": view,
                "total_records": len(entries), "selected_records": len(selected),
                "selection_meaning": "Explicit filters only; candidate groups may overlap. Empty selection is not full coverage."}
    response = compact_items_page(metadata=metadata, items=items, page_token=page_token, max_chars=max_chars,
                                  token_context={"kind": "coverage-read", "binding": binding.model_dump(mode="json"),
                                                 "input_id": input_id, "sha256": digest, "view": view,
                                                 "repo_id": repo_id, "path": path, "depth": depth,
                                                 "gap_ids": None if gap_ids is None else sorted(set(gap_ids))})
    # The shared pager bounds items; also enforce the empty-page metadata case.
    if len(json.dumps(response, ensure_ascii=False, separators=(",", ":"))) > max_chars:
        raise SourceAccessError("Coverage 返回元数据超出 max_chars，请增加本页预算")
    return response
