"""Planning allocation and bound handoff regressions, without model calls."""
import json
import tempfile
import unittest
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.cli.adapter_api import bind_action, settle_action
from pangea_agent.cli.run_module_analysis import run_module_analysis
from pangea_agent.cli.source_first_api import plan_write, result_write, work_finish
from pangea_agent.graph.result_store import read_result
from pangea_agent.inventory.source_access import input_read, task_open, SourceAccessError


class PlanningHandoff(unittest.TestCase):
    def create(self, *, legacy=False):
        temp = tempfile.TemporaryDirectory(prefix="planning-handoff-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        repo = self.root / "repositories/demo/src"
        repo.mkdir(parents=True)
        for name in ("one", "two"):
            (repo / f"{name}.c").write_text(f"int {name}(void) {{ return 1; }}\n")
        request = self.root / "request.json"
        write_json(request, {"data_root": str(self.root), "repository": "demo", "target": "handoff",
                             "source_scope": ["src"], "analysis_profile": "behavior-test-v2",
                             "analysis_settings": {"scenario": "module-analysis", "mode": "speed"}})
        created = run_module_analysis(str(request))
        self.run_id = created["run_id"]
        self.run = self.root / "runs" / self.run_id
        self.action = created["agent_actions"][0]["action_id"]
        progress = read_json(self.run / "progress.json")
        self.plan_path = Path(progress["actions"][self.action]["task_path"])
        task = read_json(self.plan_path)
        self.assertEqual(task["planning_policy"], "allocation-only-v1")
        if legacy:
            task.pop("planning_policy")
            write_json(self.plan_path, task)
        bind_action(str(self.root), self.run_id, self.action, "planner")
        self.revision = 0
        self.units = []
        for name in ("one", "two"):
            unit = {"title": name, "purpose": "business responsibility " + name,
                    "owned_files": [{"repo_id": "demo", "path": f"src/{name}.c"}]}
            response = plan_write(str(self.root), self.run_id, self.action, "planner",
                                  expected_revision=self.revision, unit=unit)
            self.revision = response["revision"]
            self.units.append({**unit, "unit_id": response["unit_id"]})
        self.old_plan_record = read_result(task["result_path"]).records[0].record_id

    def notes(self, records):
        output = result_write(str(self.root), self.run_id, self.action, "planner",
                              expected_revision=self.revision, records=records)
        self.revision = output["revision"]
        return read_result(read_json(self.plan_path)["result_path"]).records[-len(records):]

    def finish(self):
        work_finish(str(self.root), self.run_id, self.action, "planner", revision=self.revision)
        settle_action(str(self.root), self.run_id, self.action)
        self.progress = read_json(self.run / "progress.json")
        return [a for a in self.progress["actions"].values() if a["role"] == "analysis"]

    def read_handoff(self, action):
        bind_action(str(self.root), self.run_id, action["action_id"], "worker")
        opened = task_open(str(self.root), self.run_id, action["action_id"], "worker")
        self.assertIn("planning_handoff", {item["input_id"] for item in opened["task"]["inputs"]})
        cursor, pages = None, []
        while True:
            page = input_read(str(self.root), self.run_id, action["action_id"], "worker",
                              input_id="planning_handoff", cursor=cursor, max_chars=127)
            pages.append(page["text"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertGreater(len(pages), 1)
        return json.loads("".join(pages))

    def test_global_relevant_unresolved_and_unknown_relations_reach_bound_worker(self):
        self.create()
        self.notes([
            {"kind": "summary", "body": "global-boundary"},
            {"kind": "note", "body": "shared-business-boundary", "relates_to": [u["unit_id"] for u in self.units]},
            {"kind": "note", "body": {"detail": "first-only"}, "relates_to": [self.units[0]["unit_id"]],
             "evidence": [{"handle": "literal-evidence"}]},
            {"kind": "unresolved", "body": "second-unknown-entry", "relates_to": [self.units[1]["unit_id"]]},
            {"kind": "note", "body": "unknown-relation", "relates_to": ["missing-unit"]},
            {"kind": "note", "body": "odd-relation", "relates_to": {"retain": "verbatim"}},
        ])
        actions = self.finish()
        one, two = [self.read_handoff(action) for action in actions]
        self.assertEqual(one["origin"]["task_id"], "planner")
        self.assertEqual(one["origin"]["action_id"], self.action)
        self.assertEqual(one["origin"]["revision"], self.progress["accepted_revisions"][self.action])
        for handoff in (one, two):
            by_body = {json.dumps(item["record"]["body"]): item for item in handoff["records"]}
            self.assertEqual(by_body['"global-boundary"']["scope"], "global")
            self.assertEqual(by_body['"shared-business-boundary"']["scope"], "unit")
            self.assertEqual(by_body['"shared-business-boundary"']["record"]["relates_to"], [u["unit_id"] for u in self.units])
            self.assertEqual(by_body['"unknown-relation"']["scope"], "unresolved_relation")
            self.assertEqual(by_body['"odd-relation"']["record"]["relates_to"], {"retain": "verbatim"})
        self.assertIn("first-only", json.dumps(one))
        self.assertNotIn("first-only", json.dumps(two))
        self.assertNotIn("second-unknown-entry", json.dumps(one))
        self.assertIn("second-unknown-entry", json.dumps(two))
        self.assertIn('"kind": "unresolved"', json.dumps(two))
        self.assertIn("literal-evidence", json.dumps(one))
        self.assertEqual(self.progress["review_policy"], "disabled")
        task = read_json(Path(actions[0]["task_path"]))
        with self.assertRaises(SourceAccessError):
            input_read(str(self.root), self.run_id, actions[0]["action_id"], "other-worker", input_id="planning_handoff")
        self.assertNotIn("records", task)  # independent input, not duplicated inline

    def test_revisions_supersession_and_old_plan_record_relations(self):
        self.create()
        old = self.notes([{"kind": "note", "body": "retired-boundary"}])[0]
        self.notes([{"kind": "note", "body": "current-boundary", "supersedes": [old.record_id]},
                    {"kind": "evidence", "body": "old-handle-still-related", "relates_to": [self.old_plan_record]}])
        response = plan_write(str(self.root), self.run_id, self.action, "planner", expected_revision=self.revision,
                              unit={**self.units[0], "purpose": "revised business responsibility"})
        self.revision = response["revision"]
        one, two = [self.read_handoff(action) for action in self.finish()]
        self.assertNotIn("retired-boundary", json.dumps(one))
        self.assertIn("current-boundary", json.dumps(one))
        self.assertIn("old-handle-still-related", json.dumps(one))
        self.assertNotIn("old-handle-still-related", json.dumps(two))
        plans = [x["record"] for x in one["records"] if x["record"]["kind"] == "unit_plan"]
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["body"]["purpose"], "revised business responsibility")

    def test_old_frozen_task_does_not_gain_handoff(self):
        self.create(legacy=True)
        before = self.plan_path.read_bytes()
        self.notes([{"kind": "unresolved", "body": "legacy"}])
        actions = self.finish()
        self.assertEqual(before, self.plan_path.read_bytes())
        for action in actions:
            task = read_json(Path(action["task_path"]))
            self.assertNotIn("planning_policy", task)
            self.assertNotIn("planning_handoff", {x["input_id"] for x in task["inputs"]})
        self.assertFalse((self.run / "inputs/planning-handoff").exists())


if __name__ == "__main__":
    unittest.main()
