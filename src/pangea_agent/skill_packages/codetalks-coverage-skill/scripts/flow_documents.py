"""Copy explicit flow blocks from Markdown; never infer workflow semantics."""
import json
import re
from pathlib import Path


def enrich_flows(data, root):
    result = dict(data)
    flows, warnings = [], []
    for item in data.get("business_flows", []):
        if not isinstance(item, dict):
            flows.append(item)
            continue
        flow = dict(item)
        relative = item.get("document_path")
        if relative:
            try:
                document = (root / relative).resolve()
                document.relative_to(root.resolve())
                document.relative_to((root / "活文档/流程讲解").resolve())
                blocks = re.findall(r"^```pangea-flow\s*\n([\s\S]*?)^```\s*$", document.read_text(encoding="utf-8-sig"), re.M)
                if len(blocks) != 1:
                    raise ValueError("需要一个 pangea-flow 内容块")
                details = json.loads(blocks[0])
                if details.get("flow_id") != item.get("flow_id"):
                    raise ValueError("流程 ID 不一致")
                if not isinstance(details.get("mainline_steps"), list) or not isinstance(details.get("branches"), list):
                    raise ValueError("主干/分支字段不可读取")
                if any(not isinstance(row, dict) for row in details["mainline_steps"] + details["branches"]):
                    raise ValueError("主干/分支条目不可读取")
                flow.update(details)
                flow["document_path"] = relative
                flow["document_status"] = "parsed"
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                flow["document_status"] = "unparsed"
                warnings.append(f"{item.get('flow_id')}: {exc}")
        flows.append(flow)
    result["business_flows"] = flows
    result["flow_document_warnings"] = warnings
    return result
