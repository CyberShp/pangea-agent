"""Check declared source coordinates, never infer reachability or risk severity."""
from __future__ import annotations
import re
from pangea_agent.evidence_quotes import parse_source_ref

SOURCE_FACT_CONTRACT = "source-facts-v1"


class DeferredSourceResolver:
    """Load frozen inputs only if a record actually declares source coordinates."""
    def __init__(self, context):
        self.context = context

    def lines(self, repo, path):
        return self.context.resolver.lines(repo, path)


def source_fact_diagnostics(body, resolver):
    if not isinstance(body, dict):
        return []
    warnings = []
    def warn(field, code, message):
        warnings.append({"kind": "source_fact_hint", "field": field, "code": code, "message": message})
    def walk(value, field, depth=0):
        if depth > 32:
            warn(field, "inspection_limit", "结构深度超过核对上限，未检查的引用保持待确认。")
            return
        if isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{field}[{i}]", depth + 1)
        elif isinstance(value, dict):
            refs = value.get("source_evidence", [])
            if isinstance(refs, list):
                for i, ref in enumerate(refs):
                    location = f"{field}.source_evidence[{i}]"
                    try:
                        repo, path, start, end = parse_source_ref(ref)
                        lines = resolver.lines(repo, path)
                        if not 1 <= start <= end <= len(lines):
                            raise ValueError(f"行号越界或倒序；冻结文件共 {len(lines)} 行")
                    except (ValueError, OSError) as exc:
                        warn(location, "invalid_source_reference", str(exc))
            # Only the explicit path/symbol tuple is checked. Human descriptions
            # and compound semantic_key strings are never parsed as declarations.
            if all(isinstance(value.get(k), str) and value[k] for k in ("repo_id", "path", "symbol")):
                try:
                    text = "\n".join(resolver.lines(value["repo_id"], value["path"]))
                    if not re.search(r"(?<![\w$])" + re.escape(value["symbol"]) + r"(?![\w$])", text):
                        warn(f"{field}.symbol", "symbol_not_found", "声明符号未在对应冻结源码中出现；宏展开、别名或生成符号需原 worker 说明。文本出现亦不证明函数定义或可达性。")
                except (ValueError, OSError) as exc:
                    warn(f"{field}.path", "invalid_symbol_source", str(exc))
            for key, child in value.items():
                if key != "source_evidence" and isinstance(child, (dict, list)):
                    walk(child, f"{field}.{key}", depth + 1)
    walk(body, "body")
    return warnings
