"""Exact topology and real frozen-Run integration checks; no LLM calls."""
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.cli.run_module_analysis import run_module_analysis
from pangea_agent.flow_schema import check_flow_structure, flow_stability_warnings, normalize_flow
from pangea_agent.flow_stability import (
    canonical_flow, compare_flows, compare_inputs, freeze_flow_inputs,
    publish_flow_baseline,
)
from pangea_agent.graph.nodes.source_first import _prepare_review, _source_first_advance
from pangea_agent.graph.result_store import read_result
from pangea_agent.graph.workflow_store import load_progress
from pangea_agent.models.analysis import ActionState


def flow():
    return {"format_version": "behavior-flow-v1", "flow_id": "demo:connect", "layout_version": "stable-v1",
            "main_path_ids": ["success"], "title": "Connect", "role": "main",
            "nodes": [{"id": "a", "semantic_key": "demo:a.c:connect|entry", "kind": "entry", "label": "Start"},
                      {"id": "b", "semantic_key": "demo:a.c:connect|accept", "kind": "exit", "label": "Accepted"}],
            "edges": [{"edge_id": "allowed", "source_step_key": "a", "target_step_key": "b", "condition": "allowed"},
                      {"edge_id": "cached", "source_step_key": "a", "target_step_key": "b", "condition": "cached"}],
            "paths": [{"path_id": "success", "node_ids": ["a", "b"], "edge_ids": ["allowed"]},
                      {"path_id": "reuse", "node_ids": ["a", "b"], "edge_ids": ["cached"]}]}


def diff(old, new):
    return compare_flows([{"body": old}], [{"body": new}])[0][0]


class TopologyChecks(unittest.TestCase):
    def test_permutation_is_stable_and_label_changes_are_presentation_only(self):
        old, new = flow(), flow()
        for field in ("nodes", "edges", "paths"):
            new[field].reverse()
        self.assertEqual(canonical_flow(old), canonical_flow(new))
        self.assertEqual(diff(old, new)["status"], "unchanged")
        new["nodes"][0]["label"] = "Longer business wording"
        self.assertEqual(diff(old, new)["status"], "unchanged")
        self.assertTrue(diff(old, new)["presentation_changed"])
        self.assertTrue(diff(old, new)["content_changed"])
        self.assertIn("Longer business wording", str(diff(old, new)["content_changes"]))
        self.assertEqual(old, flow())

    def test_semantic_node_identity_and_parallel_edges(self):
        old, new = flow(), flow()
        new["nodes"][0]["id"] = "renumbered"
        for edge in new["edges"]:
            edge["source_step_key"] = "renumbered"
        for path in new["paths"]:
            path["node_ids"][0] = "renumbered"
        self.assertEqual(diff(old, new)["status"], "unchanged")
        new["edges"][0]["condition"] = "allowed and enabled"
        changed = diff(old, new)
        self.assertEqual(changed["status"], "changed")
        self.assertEqual(changed["edges"]["changed"][0]["identity"], "allowed")
        new["paths"][0]["edge_ids"] = ["cached"]
        self.assertEqual(len(diff(old, new)["paths"]["changed"]), 1)

    def test_optional_metadata_remains_advisory_and_legacy_unchanged(self):
        legacy = flow()
        for key in ("layout_version", "main_path_ids"):
            legacy.pop(key)
        self.assertEqual(canonical_flow(legacy), legacy)
        self.assertEqual(flow_stability_warnings(legacy), [])
        self.assertEqual(diff(legacy, legacy)["status"], "unresolved")
        for value in (None, 3, "success", ["missing"]):
            new = flow()
            new["main_path_ids"] = value
            self.assertEqual(check_flow_structure(new), [])
            self.assertEqual(diff(flow(), new)["status"], "unresolved")
            parsed, warnings = normalize_flow(new)
            self.assertTrue(parsed)
            self.assertTrue(warnings)

    def test_unknown_metadata_is_never_claimed_identical(self):
        fingerprint = {"dimension_hashes": {k: "same" for k in ("source", "scope", "assets", "methodology", "model", "objective")},
                       "model": {"model_id": "test", "settings": {"provider": "host"}, "metadata_status": "partial"}}
        self.assertEqual(compare_inputs(fingerprint, fingerprint)["status"], "unknown")
        other = deepcopy(fingerprint)
        other["model"]["model_id"] = "other"
        self.assertEqual(compare_inputs(fingerprint, other)["dimensions"]["model"], "changed")
        other["dimension_hashes"]["source"] = "changed"
        self.assertEqual(compare_inputs(fingerprint, other)["status"], "different_inputs")


class FrozenRunChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pangea-flow-stability-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        repo = self.root / "repositories/demo"
        repo.mkdir(parents=True)
        (repo / "a.c").write_text("int connect(int enabled) { return enabled ? 0 : -1; }\n")

    def create(self, run_id, **extra):
        contract = {"run_id": run_id, "data_root": str(self.root), "repository": "demo", "target": "connect",
                    "source_scope": ["."], "analysis_profile": "behavior-test-v2", "model_id": "test-model",
                    "model_settings": {"provider": "host", "api_key": "not-a-real-key"},
                    "analysis_settings": {"scenario": "branch-analysis", "mode": "depth"}, **extra}
        path = self.root / f"{run_id}.json"
        write_json(path, contract)
        run_module_analysis(str(path))
        run = self.root / "runs" / run_id
        return {"run_id": run_id, "data_root": str(self.root), "task_contract": read_json(run / "inputs/task-contract.json")}

    def accepted_analysis(self, state):
        run = self.root / "runs" / state["run_id"]
        progress = load_progress(state)
        progress.actions.clear()
        action_id = f"{state['run_id']}:analysis:one"
        task_path = run / "agent-tasks/analysis-one.json"
        result_path = run / "agent-results/analysis-one.json"
        task = {"unit_id": "one", "result_path": str(result_path)}
        write_json(task_path, task)
        write_json(result_path, {"format_version": "pangea-notes-v1", "binding": {"run_id": state["run_id"],
                   "data_root": str(self.root), "action_id": action_id, "task_id": "analysis-worker"},
                   "revision": 1, "records": [{"record_id": "rec-000001", "kind": "flow", "body": flow(), "created_revision": 1}]})
        action = ActionState(action_id=action_id, action="dispatch_agent", role="analysis", stage="unit_analysis",
                             status="accepted", task_id="analysis-worker", task_path=str(task_path))
        progress.actions[action_id] = action
        progress.accepted_revisions[action_id] = 1
        return progress, [({**action.model_dump(mode="json"), "task": task}, read_result(result_path))]

    def parent(self):
        state = self.create("parent")
        progress, records = self.accepted_analysis(state)
        progress.lifecycle_status, progress.quality_status = "complete", "PASS"
        write_json(self.root / "runs/parent/progress.json", progress.model_dump(mode="json"))
        snapshot = publish_flow_baseline(state, progress.model_dump(mode="json"), records)
        self.assertTrue(snapshot["eligible"])
        return snapshot

    def test_real_graph_blind_then_comparison_and_frozen_parent_independence(self):
        parent = self.parent()
        state = self.create("child", flow_baseline={k: parent[k] for k in ("run_id", "version_id")})
        run = self.root / "runs/child"
        self.assertNotIn("api_key", state["task_contract"]["model_settings"])
        progress, _ = self.accepted_analysis(state)
        _prepare_review(state, progress)
        review = progress.actions["child:review"]
        task = read_json(Path(review.task_path))
        ids = {i["input_id"] for i in task["inputs"]}
        self.assertIn("flow_stability", ids)
        self.assertNotIn("flow_baseline", ids)
        self.assertNotIn("flow_comparison", ids)
        self.root.joinpath("runs/parent").rename(self.root / "parent-offline")
        review.status, review.task_id, progress.stage = "settled", "review-worker", "reviewing"
        result = read_json(Path(task["result_path"]))
        result["binding"]["task_id"] = "review-worker"
        write_json(Path(task["result_path"]), result)
        _source_first_advance(state, progress)
        comparison = read_json(run / "agent-tasks/source-first-comparison.json") if (run / "agent-tasks/source-first-comparison.json").exists() else read_json(Path(progress.actions["child:comparison-review"].task_path))
        ids = {i["input_id"] for i in comparison["inputs"]}
        self.assertIn("flow_baseline", ids)
        self.assertIn("flow_comparison", ids)
        summary = read_json(run / "inputs/flow-comparison.json")
        self.assertEqual(summary["summary"]["unchanged"], 1)
        self.assertEqual(summary["comparability"]["dimensions"]["source"], "same")
        self.assertEqual(summary["comparability"]["dimensions"]["model"], "unknown")

    def test_explicit_bad_version_stays_bad_after_partial_preparation(self):
        parent = self.parent()
        with self.assertRaisesRegex(ValueError, "身份不匹配"):
            self.create("bad", flow_baseline={"run_id": "parent", "version_id": "wrong"})
        run = self.root / "runs/bad"
        self.assertTrue((run / "inputs/flow-stability.json").is_file())
        state = {"run_id": "bad", "data_root": str(self.root), "task_contract": read_json(run / "inputs/task-contract.json")}
        with self.assertRaisesRegex(ValueError, "身份不匹配"):
            freeze_flow_inputs(state, read_json(run / "inputs/source-manifest.json"), {"flow_layout_version": "stable-v1"})

    def test_input_content_and_unknown_model_metadata(self):
        first, second = self.create("one"), self.create("two")
        a = read_json(self.root / "runs/one/inputs/flow-stability.json")
        b = read_json(self.root / "runs/two/inputs/flow-stability.json")
        self.assertEqual(a["fingerprint"], b["fingerprint"])
        (self.root / "repositories/demo/a.c").write_text("int connect(void) { return 0; }\n")
        self.create("three")
        c = read_json(self.root / "runs/three/inputs/flow-stability.json")
        self.assertEqual(compare_inputs(a, c)["dimensions"]["source"], "changed")

    def test_frozen_baseline_tamper_and_final_closure_comparison(self):
        parent = self.parent()
        state = self.create("child", flow_baseline={k: parent[k] for k in ("run_id", "version_id")},
                            analysis_settings={"scenario": "branch-analysis", "mode": "speed"})
        run = self.root / "runs/child"
        progress, records = self.accepted_analysis(state)
        _prepare_review(state, progress)
        first_round = (run / "inputs/flow-comparison.json").read_bytes()
        closure_action = deepcopy(records[0][0])
        closure_action.update(action_id="child:closure:one", stage="targeted_closure", role="closure")
        closure = records[0][1].model_copy(deep=True)
        closure.binding.action_id = closure_action["action_id"]
        closure.records[0].body["edges"][0]["condition"] = "allowed after update"
        closure.revision = 2
        progress.accepted_revisions[closure_action["action_id"]] = 2
        progress.lifecycle_status, progress.quality_status = "complete", "PASS"
        published = publish_flow_baseline(state, progress.model_dump(mode="json"), [*records, (closure_action, closure)])
        final = read_json(run / "outputs/flow-delivery-comparison.json")
        self.assertEqual(final["summary"]["changed"], 1)
        self.assertEqual(final["comparison_scope"], "delivery")
        self.assertFalse(final["reviewed"])
        self.assertEqual((run / "inputs/flow-comparison.json").read_bytes(), first_round)
        self.assertEqual(published["flows"][0]["revision"], 2)
        snapshot_path = run / "inputs/flow-baseline.json"
        tampered = read_json(snapshot_path)
        tampered["flows"][0]["body"]["title"] = "tampered frozen content"
        write_json(snapshot_path, tampered)
        with self.assertRaisesRegex(ValueError, "指纹损坏"):
            freeze_flow_inputs(state, read_json(run / "inputs/source-manifest.json"), {"flow_layout_version": "stable-v1"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
