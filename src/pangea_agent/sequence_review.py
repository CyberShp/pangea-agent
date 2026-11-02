"""Check review coverage against frozen record coordinates. Reviewer judges semantics."""


def missing_sequence_checks(flows, review_records, version_set_id, unit_id):
    covered = set()
    for record in review_records:
        note = record.body
        if (record.kind != "note" or not isinstance(note, dict)
                or note.get("format_version") != "sequence-path-review-v1"
                or note.get("version_set_id") != version_set_id or note.get("unit_id") != unit_id
                or not isinstance(note.get("record_id"), str)):
            continue
        checks = note.get("checks")
        for check in checks if isinstance(checks, list) else []:
            if (isinstance(check, dict) and isinstance(check.get("field"), str)
                    and isinstance(check.get("basis"), str) and check["basis"].strip()):
                covered.add((note["record_id"], check["field"]))

    missing = []
    for record in flows:
        if record.kind != "flow" or not isinstance(record.body, dict):
            continue
        paths = record.body.get("paths")
        fields = [f"paths[{i}]" for i in range(len(paths))] if isinstance(paths, list) else []
        sequence = record.body.get("sequence")
        fragments = sequence.get("fragments") if isinstance(sequence, dict) else None
        for i, fragment in enumerate(fragments if isinstance(fragments, list) else []):
            operands = fragment.get("operands") if isinstance(fragment, dict) else None
            if isinstance(operands, list):
                fields.extend(f"sequence.fragments[{i}].operands[{j}]" for j in range(len(operands)))
        absent = [field for field in fields or ["sequence"] if (record.record_id, field) not in covered]
        if absent:
            missing.append(f"{unit_id}/{record.record_id} 缺少时序对照依据：{', '.join(absent)}")
    return missing
