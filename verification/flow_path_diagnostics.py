"""Path diagnostics preserve records and return repair hints to the same worker."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from pangea_agent.flow_schema import check_flow_structure, flow_stability_warnings
from pangea_agent.graph.result_store import active_records, append_records, declare_completion, initialize_result, read_result
from pangea_agent.models.source_first import SourceBinding


def flow():
    return {"format_version": "behavior-flow-v1", "flow_id": "demo:connect", "layout_version": "stable-v1",
            "main_path_ids": ["success"], "title": "Connect", "role": "main",
            "nodes": [{"id": "a", "semantic_key": "demo:a.c:connect|entry", "kind": "entry", "label": "Start"},
                      {"id": "b", "semantic_key": "demo:a.c:connect|accept", "kind": "exit", "label": "Accepted"}],
            "edges": [{"edge_id": "allowed", "source_step_key": "a", "target_step_key": "b", "condition": "allowed"},
                      {"edge_id": "cached", "source_step_key": "a", "target_step_key": "b", "condition": "cached"}],
            "paths": [{"path_id": "success", "node_ids": ["a", "b"], "edge_ids": ["allowed"]},
                      {"path_id": "reuse", "node_ids": ["a", "b"], "edge_ids": ["cached"]}]}



class PathDiagnosticsChecks(unittest.TestCase):
    def test_path_hints_identify_every_bad_position_without_mutating_or_rejecting(self):
        value = flow()
        value["nodes"].append({"id": "c", "semantic_key": "demo:cleanup", "label": "Closed", "kind": "exit"})
        value["edges"].append({"edge_id": "close", "source_step_key": "b", "target_step_key": "c"})
        value["paths"][0].update(node_ids=["a", "b", "c"], edge_ids=["close"])
        original = deepcopy(value)
        warnings = "\n".join(flow_stability_warnings(value))
        self.assertIn("edge_ids 有 1 条，node_ids 有 3 步，应有 2 条", warnings)
        self.assertIn("edge_ids[0] 应连接 a → b，实际为 close（b → c）", warnings)
        self.assertIn("edge_ids[1] 应连接 b → c，实际为 未提供", warnings)
        self.assertEqual(check_flow_structure(value), [])
        self.assertEqual(value, original)

    def test_duplicate_edge_id_is_ambiguous_and_loop_occurrences_are_not_deduped(self):
        value = flow()
        value["edges"].append({"edge_id": "retry", "source_step_key": "b", "target_step_key": "a"})
        value["paths"][0].update(node_ids=["a", "b", "a", "b"], edge_ids=["allowed", "retry", "allowed"])
        self.assertEqual(flow_stability_warnings(value), [])
        value["edges"].append(deepcopy(value["edges"][0]))
        warnings = "\n".join(flow_stability_warnings(value))
        self.assertIn("edge_ids[0] 应连接 a → b，实际为 allowed（不存在或 ID 不唯一）", warnings)
        self.assertIn("edge_ids[2] 应连接 a → b，实际为 allowed（不存在或 ID 不唯一）", warnings)
        self.assertEqual(check_flow_structure(value), [])

    def test_write_returns_path_hints_and_same_worker_can_supersede_then_complete(self):
        with tempfile.TemporaryDirectory(prefix="pangea-path-hints-") as folder:
            path = Path(folder) / "result.json"
            binding = SourceBinding(run_id="run", data_root=folder, action_id="run:analysis:unit", task_id="original-worker")
            initialize_result(path, binding)
            broken = flow()
            broken["paths"][0]["edge_ids"] = []
            first = append_records(path, binding, 0, [{"kind": "flow", "body": broken}])
            self.assertTrue(first["warnings"])
            self.assertTrue(all(item["kind"] == "flow_path_hint" for item in first["warnings"]))
            self.assertTrue(all(item["record_id"] == first["record_ids"][0] for item in first["warnings"]))
            self.assertEqual(read_result(path).records[0].body, broken)
            self.assertIsNone(read_result(path).completion)
            second = append_records(path, binding, first["revision"], [{"kind": "flow", "body": flow(), "supersedes": first["record_ids"]}])
            self.assertEqual(second["warnings"], [])
            self.assertEqual([record.body for record in active_records(read_result(path))], [flow()])
            final = declare_completion(path, binding, second["revision"], complete=True)
            self.assertTrue(final["complete"])
            self.assertEqual(read_result(path).binding.task_id, "original-worker")


if __name__ == "__main__":
    unittest.main(verbosity=2)
