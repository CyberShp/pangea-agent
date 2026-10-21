"""Advisory diagram-contract-v1 checks. Never judge business completeness.

Keep classifications/fields/codes aligned with companion/diagram-contract.js.
Old frozen contracts are read tolerantly; no check here rejects a write.
"""
from __future__ import annotations

from typing import Any

DIAGRAM_CONTRACT = "diagram-contract-v1"
FORMATS = {"flow": "behavior-flow-v1", "sequence": "sequence-v1", "call_graph": "call-graph-v1"}
STATUSES = ("ready", "partial", "unknown", "not_applicable")


def diagram_diagnostics(body: Any, *, strict: bool = False) -> list[dict[str, Any]]:
    """Return field-level presentation diagnostics without changing authored facts.

    strict means the current task explicitly froze diagram-contract-v1. It only
    enables missing-field hints and the new writes:string contract, never gates.
    """
    out: list[dict[str, Any]] = []

    def emit(diagram, field, code, message, classification="invalid"):
        out.append({"kind": "diagram_format_diagnostic", "contract_version": DIAGRAM_CONTRACT,
                    "diagram": diagram, "field": field, "code": code,
                    "classification": classification,
                    "severity": "warning" if classification == "invalid" else "info",
                    "message": message})

    def check(diagram, row, key, kind, where, *, required=False, choices=None):
        field = f"{where}.{key}" if where else key
        value = row.get(key)
        if value is None:
            if strict and required:
                emit(diagram, field, "missing_field", "字段未提供；保留已保存内容，请原 worker 确认", "missing")
            return None
        valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
                 "string": isinstance(value, str), "line": type(value) is int and value > 0}[kind]
        if not valid:
            emit(diagram, field, "invalid_type", f"字段必须是 {kind}；原值保留")
            return None
        if choices and value not in choices:
            emit(diagram, field, "invalid_enum", f"字段应为 {' | '.join(choices)}；原值保留")
        return value

    def rows(diagram, row, key, where, *, required=False):
        values = check(diagram, row, key, "array", where, required=required)
        for index, item in enumerate(values or []):
            field = f"{where}.{key}[{index}]" if where else f"{key}[{index}]"
            if not isinstance(item, dict):
                emit(diagram, field, "invalid_type", "项目必须是 object；原值保留")
                continue
            yield item, field

    def strings(diagram, row, key, where, required=False):
        values = check(diagram, row, key, "array", where, required=required)
        for index, value in enumerate(values or []):
            if not isinstance(value, str):
                emit(diagram, f"{where}.{key}[{index}]", "invalid_type", "项目必须是 string；原值保留")
        return values or []

    def identities(diagram, items):
        ids = set()
        for item, where in items:
            value = check(diagram, item, "id", "string", where, required=True)
            if value is None:
                continue
            if not value.strip():
                emit(diagram, f"{where}.id", "empty_id", "id 必须是非空字符串")
            elif value in ids:
                emit(diagram, f"{where}.id", "duplicate_id", "id 重复，关系归属待确认")
            ids.add(value)
        return ids

    def reference(diagram, row, key, known, where):
        value = check(diagram, row, key, "string", where, required=True)
        if value is not None and value not in known:
            emit(diagram, f"{where}.{key}", "unknown_reference", "引用未声明的 id；原值保留")

    if not isinstance(body, dict):
        emit("flow", "body", "invalid_type", "流程 body 必须是 object；原值保留")
        return out
    for diagram in FORMATS:
        value = body if diagram == "flow" else body.get(diagram)
        where = "" if diagram == "flow" else diagram
        if value is None:
            if strict:
                emit(diagram, where, "missing_diagram", "附图资料未提供，不代表已完成", "missing")
            continue
        if not isinstance(value, dict):
            emit(diagram, where or "body", "invalid_type", "图资料必须是 object；原值保留")
            continue
        version = check(diagram, value, "format_version", "string", where, required=True)
        if version is not None and version != FORMATS[diagram]:
            emit(diagram, f"{where}.format_version" if where else "format_version",
                 "unsupported_version", "格式版本暂不支持，原始资料保留", "valid_but_unsupported")
            continue
        if diagram != "flow":
            status = check(diagram, value, "status", "string", where, required=True, choices=STATUSES)
            if status in ("unknown", "not_applicable", "partial"):
                emit(diagram, f"{where}.status", "declared_status", f"worker 声明 {status}；保留原结论", "unknown")
        check(diagram, value, "title", "string", where, required=True)
        if diagram == "flow":
            nodes = list(rows(diagram, value, "nodes", where, required=True))
            ids = identities(diagram, nodes)
            for node, path in nodes:
                check(diagram, node, "label", "string", path, required=True)
                check(diagram, node, "kind", "string", path, choices=("entry", "main", "branch", "error", "propagation", "recovery", "exit"))
            for edge, path in rows(diagram, value, "edges", where, required=True):
                for key in ("source_step_key", "target_step_key"):
                    reference(diagram, edge, key, ids, path)
            for path_row, path in rows(diagram, value, "paths", where, required=True):
                for index, ref in enumerate(strings(diagram, path_row, "node_ids", path, True)):
                    if isinstance(ref, str) and ref not in ids:
                        emit(diagram, f"{path}.node_ids[{index}]", "unknown_reference", "引用未声明的 id；原值保留")
        elif diagram == "sequence":
            participants = list(rows(diagram, value, "participants", where, required=True))
            participant_ids = identities(diagram, participants)
            for participant, path in participants:
                check(diagram, participant, "label", "string", path, required=True)
            messages = list(rows(diagram, value, "messages", where, required=True))
            message_ids = identities(diagram, messages)
            positions = {message.get("id"): index for index, message in enumerate(value.get("messages", []))
                         if isinstance(message, dict) and isinstance(message.get("id"), str)} if isinstance(value.get("messages"), list) else {}
            for message, path in messages:
                for key in ("from", "to"):
                    reference(diagram, message, key, participant_ids, path)
                check(diagram, message, "label", "string", path, required=True)
                check(diagram, message, "kind", "string", path, required=True, choices=("call", "return", "async", "self"))
                if message.get("kind") == "self" and message.get("from") != message.get("to"):
                    emit(diagram, f"{path}.kind", "self_endpoints", "self 消息的 from/to 必须相同")
                strings(diagram, message, "source_evidence", path)
            fragments = list(rows(diagram, value, "fragments", where, required=True))
            identities(diagram, fragments)
            owned = set()
            for fragment, path in fragments:
                kind = check(diagram, fragment, "kind", "string", path, required=True, choices=("alt", "opt", "loop", "par"))
                operands = list(rows(diagram, fragment, "operands", path, required=True))
                if kind in ("alt", "par") and len(operands) < 2 or kind in ("opt", "loop") and len(operands) != 1:
                    emit(diagram, f"{path}.operands", "operand_count", "分支数量与片段类型不符")
                indexes = []
                for operand, operand_path in operands:
                    check(diagram, operand, "condition", "string", operand_path, required=True)
                    operand_indexes = []
                    for index, ref in enumerate(strings(diagram, operand, "message_ids", operand_path, True)):
                        if not isinstance(ref, str):
                            continue
                        ref_path = f"{operand_path}.message_ids[{index}]"
                        if ref not in message_ids:
                            emit(diagram, ref_path, "unknown_reference", "引用未声明的 id；原值保留")
                        else:
                            indexes.append(positions[ref])
                            operand_indexes.append(positions[ref])
                        if ref in owned:
                            emit(diagram, ref_path, "overlapping_fragment", "消息同时属于多个分支或片段；归属待确认")
                        owned.add(ref)
                    if any(position <= operand_indexes[index - 1] for index, position in enumerate(operand_indexes) if index):
                        emit(diagram, f"{operand_path}.message_ids", "conflicting_message_order", "同一分支内的消息顺序与 messages 冲突；保留原始关系")
                # The fragment occupies a contiguous span. Operand declaration
                # order is independent of the messages array's branch order.
                ordered = sorted(set(indexes))
                if ordered and ordered[-1] - ordered[0] + 1 != len(ordered):
                    emit(diagram, f"{path}.operands", "noncontiguous_fragment", "片段消息在 messages 中不连续；保留原始关系")
        else:
            functions = list(rows(diagram, value, "functions", where, required=True))
            ids = identities(diagram, functions)
            reference(diagram, value, "root", ids, where)
            for function, path in functions:
                check(diagram, function, "kind", "string", path, required=True, choices=("function", "macro", "external"))
                for key in ("label", "signature", "file"):
                    check(diagram, function, key, "string", path, required=True)
                check(diagram, function, "line", "line", path)
            for call, path in rows(diagram, value, "calls", where, required=True):
                for key in ("from", "to"):
                    reference(diagram, call, key, ids, path)
                strings(diagram, call, "args", path, True)
                for key in ("file", "returns"):
                    check(diagram, call, key, "string", path, required=True)
                check(diagram, call, "line", "line", path)
            for variable, path in rows(diagram, value, "variables", where, required=True):
                reference(diagram, variable, "function", ids, path)
                for key in ("name", "compute"):
                    check(diagram, variable, key, "string", path, required=True)
                strings(diagram, variable, "reads", path, True)
                if not strict and isinstance(variable.get("writes"), list) and all(isinstance(item, str) for item in variable["writes"]):
                    emit(diagram, f"{path}.writes", "legacy_writes_array", "旧冻结合同的 writes 数组按原项目兼容展示", "valid_but_unsupported")
                else:
                    check(diagram, variable, "writes", "string", path, required=True)
                check(diagram, variable, "line", "line", path)
        if diagram != "flow":
            strings(diagram, value, "notes", where, True)
    return out
