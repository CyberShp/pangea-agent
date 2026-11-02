"""Replay sequence review coverage, optionally against a frozen login analysis Run."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

from pangea_agent.graph.result_store import active_records, read_result
from pangea_agent.sequence_review import missing_sequence_checks


def record(kind, body, record_id):
    return SimpleNamespace(kind=kind, body=body, record_id=record_id)


def receipt(flow, fields, unit="u", version="v"):
    return record("note", {"record_id": flow.record_id, "unit_id": unit,
                           "version_set_id": version, "format_version": "sequence-path-review-v1",
                           "checks": [{"field": key, "basis": "src/login.c:12 收到响应后才进入解析"}
                                      for key in fields]}, "note")


class PathReview(unittest.TestCase):
    def test_partial_wrong_version_and_replaced_graph_do_not_pass(self):
        body = {"paths": [{"path_id": "success"}], "sequence": {"fragments": [
            {"kind": "alt", "operands": [{}, {}]}]}}
        flow = record("flow", body, "final")
        fields = ["paths[0]", "sequence.fragments[0].operands[0]", "sequence.fragments[0].operands[1]"]
        for note in [receipt(flow, fields[:-1]), receipt(flow, fields, version="old"),
                     receipt(flow, fields, unit="another"), receipt(record("flow", body, "old"), fields)]:
            self.assertTrue(missing_sequence_checks([flow], [note], "v", "u"))
        self.assertFalse(missing_sequence_checks([flow], [receipt(flow, fields)], "v", "u"))

    def test_linear_flow_and_empty_basis(self):
        flow = record("flow", {}, "linear")
        note = receipt(flow, ["sequence"])
        self.assertFalse(missing_sequence_checks([flow], [note], "v", "u"))
        note.body["checks"][0]["basis"] = " "
        self.assertTrue(missing_sequence_checks([flow], [note], "v", "u"))


def replay(run):
    results = run / "agent-results/source-first"
    from pangea_agent.agent_io import read_json
    from pangea_agent.cli.source_first_api import _comparison_diagnostics

    version = read_json(run / "inputs/source-first-comparison-version-set.json")["version_set_id"]
    comparison = read_result(results / "comparison.json").model_copy(deep=True)
    decision = next(r for r in reversed(active_records(comparison)) if r.kind == "review_decision")
    decision.body["disposition"] = "pass"
    task = {"version_set_id": version}
    assert not _comparison_diagnostics(run, task, comparison)
    task["record_contracts"] = {"sequence_review": "sequence-path-review-v1"}
    failures = _comparison_diagnostics(run, task, comparison)
    assert len(failures) == 6 and all("缺少时序对照依据" in err for err in failures), failures
    decision.body["disposition"] = "unresolved"
    assert not _comparison_diagnostics(run, task, comparison)
    print("Frozen login regression: all six active flow records lack path review receipts.")


if __name__ == "__main__":
    if len(sys.argv) == 2:
        replay(Path(sys.argv.pop()))
    unittest.main()
