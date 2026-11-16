"""Compare explicit cross-unit declarations without inventing interface identity."""
from __future__ import annotations
from collections import defaultdict
from pangea_agent.graph.result_store import active_records

BOUNDARY_CONTRACT = "interface-boundary-reconciliation-v1"


def reconcile_boundaries(delivery):
    groups = defaultdict(list)
    diagnostics = []
    identifiers = defaultdict(list)
    rows = []
    for action, result in delivery:
        unit = str(action.get("task", {}).get("unit_id") or "")
        for record in active_records(result):
            if not isinstance(record.body, dict):
                continue
            identity = {"unit_id": unit, "action_id": result.binding.action_id,
                        "task_id": result.binding.task_id, "record_id": record.record_id,
                        "record_revision": record.created_revision}
            rows.append((identity, record.body))
            for key in ("risk_id", "case_id", "flow_id"):
                value = record.body.get(key)
                if isinstance(value, str) and value:
                    identifiers[(unit, key, value)].append(identity)
            assumptions = record.body.get("interface_assumptions", [])
            if not isinstance(assumptions, list):
                diagnostics.append({**identity, "code": "invalid_interface_assumptions", "field": "body.interface_assumptions"})
                continue
            for index, item in enumerate(assumptions):
                field = f"body.interface_assumptions[{index}]"
                if (not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k] for k in ("repo_id", "path", "symbol"))
                        or item.get("role") not in {"producer", "consumer"}
                        or not isinstance(item.get("error_codes"), list)
                        or any(not isinstance(code, str) for code in item["error_codes"])):
                    diagnostics.append({**identity, "code": "incomplete_interface_identity", "field": field})
                    continue
                key = (item["repo_id"], item["path"], item["symbol"])
                groups[key].append({**identity, "field": field, "role": item["role"],
                                    "error_codes": sorted(set(item["error_codes"])),
                                    "source_evidence": item.get("source_evidence", [])})
    for identity, body in rows:
        for field, key in (("risk_refs", "risk_id"), ("flow_refs", "flow_id"), ("linked_test_case_ids", "case_id")):
            refs = body.get(field, [])
            if not isinstance(refs, list):
                continue  # Existing structure diagnostics own this shape.
            for index, ref in enumerate(refs):
                unit = identity["unit_id"]
                if isinstance(ref, dict):
                    unit, value = ref.get("unit_id", unit), ref.get(key)
                else:
                    value = ref
                if not isinstance(unit, str) or not isinstance(value, str):
                    diagnostics.append({**identity, "code": "invalid_record_reference", "field": f"body.{field}[{index}]"})
                    continue
                matches = identifiers.get((unit, key, value), [])
                if len(matches) != 1:
                    diagnostics.append({**identity, "code": "reference_missing" if not matches else "reference_ambiguous",
                                        "field": f"body.{field}[{index}]", "target": {"unit_id": unit, key: value}})
    comparisons = []
    for key, declarations in sorted(groups.items()):
        producers = [x for x in declarations if x["role"] == "producer"]
        consumers = [x for x in declarations if x["role"] == "consumer"]
        for consumer in consumers:
            peers = [x for x in producers if x["unit_id"] != consumer["unit_id"]]
            if not peers:
                diagnostics.append({**consumer, "code": "producer_evidence_pending", "interface": key})
            for producer in peers:
                difference = sorted(set(producer["error_codes"]) ^ set(consumer["error_codes"]))
                comparisons.append({"interface": key, "producer": producer, "consumer": consumer,
                                    "status": "declared_codes_differ" if difference else "declared_codes_match",
                                    "differing_codes": difference})
    return {"format_version": BOUNDARY_CONTRACT, "status": "declarations_compared" if comparisons else "evidence_pending",
            "scope": "Only explicit identities and error-code sets; matching does not prove reachability or correctness, differences require original workers to check conditions.",
            "comparisons": comparisons, "diagnostics": diagnostics}
