"""Lossless, explicit Planning record routing, not semantic interpretation."""

from pangea_agent.graph.result_store import active_records


def unit_handoff(result, units: list[dict], unit: dict) -> dict:
    # Include old record handles in routing: updating a unit does not change
    # the meaning of an earlier note's explicit relation to that unit.
    owners = {item["unit_id"]: item["unit_id"] for item in units}
    for record in result.records:
        if record.kind == "unit_plan" and isinstance(record.body, dict):
            owner = record.body.get("unit_id")
            if isinstance(owner, str) and owner in owners:
                owners[record.record_id] = owner
    selected = []
    for record in active_records(result):
        if record.kind == "completion":
            continue
        if record.kind == "unit_plan":
            if record.record_id != unit["plan_record_id"]:
                continue
            scope = "unit"
        else:
            relations = record.relates_to
            if relations == []:
                scope = "global"
            elif not isinstance(relations, list) or any(
                not isinstance(ref, str) or ref not in owners for ref in relations
            ):
                # Preserve uncertain relation shapes/targets rather than
                # guessing a recipient or dropping the worker's evidence.
                scope = "unresolved_relation"
            elif unit["unit_id"] in {owners[ref] for ref in relations}:
                scope = "unit"
            else:
                continue
        selected.append({"scope": scope, "record": record.model_dump(mode="json")})
    return {
        "format_version": "planning-handoff-v1",
        "origin": {**result.binding.model_dump(mode="json"), "revision": result.revision},
        "unit_id": unit["unit_id"],
        "records": selected,
        "usage": "规划仅分配职责。保留原始证据与不确定性，由本单元重新核对源码；unresolved_relation 不代表全局适用。",
    }
