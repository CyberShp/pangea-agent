"""Frozen Coverage navigation and lossless paging through real Graph bindings."""
import json
import tempfile
import unittest
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.cli.adapter_api import bind_action
from pangea_agent.cli.run_module_analysis import run_module_analysis
from pangea_agent.inventory.coverage_access import coverage_read
from pangea_agent.inventory.source_access import SourceAccessError


class PlanningCoverageQuery(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="planning-coverage-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("src/tcp/auth.c", "src/tcp/queue.c", "src/tcp2/other.c"):
            file = self.root / "repositories/demo" / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("int entry(void) { return 1; }\n", encoding="utf-8")
        request = self.root / "request.json"
        write_json(request, {"data_root": str(self.root), "repository": "demo", "target": "coverage navigation",
                             "source_scope": ["src"], "analysis_profile": "behavior-test-v2",
                             "analysis_settings": {"scenario": "module-analysis", "mode": "speed"}})
        created = run_module_analysis(str(request))
        self.run_id = created["run_id"]
        self.run = self.root / "runs" / self.run_id
        self.action = created["agent_actions"][0]["action_id"]
        progress = read_json(self.run / "progress.json")
        self.task_path = Path(progress["actions"][self.action]["task_path"])
        bind_action(str(self.root), self.run_id, self.action, "planner")
        self.gaps = [
            {"coverage_id": "function", "coverage_type": "function", "count": 0,
             "path": "src/tcp/auth.c", "source": "summary", "function": "entry",
             "matches": [{"repo_id": "demo", "path": "src/tcp/auth.c", "line": 1}]},
            {"coverage_id": "branch", "coverage_type": "branch", "count": 9,
             "true_count": 9, "false_count": 0, "branch_id": "B1", "condition": "valid",
             "path": "src/tcp/auth.c", "matches": [{"repo_id": "demo", "path": "src/tcp/auth.c", "line": 2}]},
            {"coverage_id": "other", "coverage_type": "line", "count": 0, "line": 1,
             "path": "src/tcp2/other.c", "matches": [{"repo_id": "demo", "path": "src/tcp2/other.c", "line": 1}]},
        ]
        self.unknown = {"coverage_id": "unknown", "coverage_type": "branch", "coverage_state": "indeterminate",
                        "count": None, "block": "1", "branch": "0", "source": "llt", "path": "src/tcp/queue.c",
                        "matches": [{"repo_id": "demo", "path": "src/tcp/queue.c", "line": 1}]}
        self.ambiguous = {"coverage_id": "ambiguous", "coverage_type": "function", "count": 0,
                          "path": "auth.c", "matches": [{"repo_id": "demo", "path": "src/tcp/auth.c", "line": 1},
                                                         {"repo_id": "demo", "path": "src/tcp/queue.c", "line": 1}]}
        self.unlocated = {"coverage_id": "unlocated", "coverage_type": "function", "count": 0,
                          "function": "missing", "matches": []}
        self.summary = {"format_version": "coverage-match-summary-v1", "sources": [{"status": "partial", "source": "llt"}],
                        "matched": [*self.gaps, self.unknown], "ambiguous": [self.ambiguous],
                        "unmatched": [self.unlocated], "note": "unknown is not zero"}
        write_json(self.run / "inputs/coverage-gaps.json", self.gaps)
        write_json(self.run / "inputs/coverage-match-summary.json", self.summary)

    def query(self, **kwargs):
        return coverage_read(str(self.root), self.run_id, self.action, "planner", **kwargs)

    def all_items(self, **kwargs):
        items, fragments, token = [], {}, None
        max_chars = kwargs.setdefault("max_chars", 1400)
        for _ in range(1000):
            page = self.query(page_token=token, **kwargs)
            self.assertLessEqual(len(json.dumps(page, ensure_ascii=False, separators=(",", ":"))), max_chars)
            items.extend(page["items"])
            fragment = page["item_fragment"]
            if fragment:
                previous = fragments.get(fragment["item_index"], "")
                self.assertEqual(len(previous), fragment["char_start"])
                value = previous + fragment["text"]
                self.assertEqual(len(value), fragment["char_end"])
                fragments[fragment["item_index"]] = value
                if fragment["complete"]:
                    items.append(json.loads(value))
            token = page["next_page_token"]
            if token is None:
                return items
        self.fail("Coverage pagination did not finish")

    def test_path_drilldown_and_real_id_projection_keep_branch_side(self):
        groups = self.all_items(view="summary", path="src/tcp")
        self.assertEqual([(i["value"]["path"], i["value"]["distinct_records"]) for i in groups],
                         [("src/tcp/auth.c", 2)])
        indexes = self.all_items(view="index", path="src/tcp", repo_id="demo")
        self.assertEqual([item["value"]["coverage_id"] for item in indexes], ["function", "branch"])
        self.assertEqual(indexes[1]["value"]["false_count"], 0)
        self.assertEqual(indexes[1]["value"]["count"], 9)
        selected = self.all_items(view="records", gap_ids=["branch"])
        self.assertEqual([item["value"] for item in selected], [self.gaps[1]])
        self.assertEqual(self.all_items(view="records", gap_ids=["invented"]), [])
        self.assertEqual(self.all_items(view="records", path="tcp"), [])  # no suffix guessing

    def test_unknown_and_ambiguous_records_survive_and_groups_are_not_owners(self):
        records = self.all_items(input_id="coverage_match_summary", view="records")
        self.assertEqual([item["value"] for item in records if item["item_type"] == "coverage_record"],
                         [*self.gaps, self.unknown, self.unlocated, self.ambiguous])
        self.assertEqual(records[-1]["value"], {key: self.summary[key] for key in ("format_version", "sources", "note")})
        filtered = self.all_items(input_id="coverage_match_summary", view="records", path="src/tcp/auth.c")
        self.assertEqual(sum(item.get("value") == self.ambiguous for item in filtered), 1)
        self.assertEqual(next(item for item in filtered if item.get("value") == self.ambiguous)["value"]["matches"],
                         self.ambiguous["matches"])
        groups = self.all_items(input_id="coverage_match_summary", view="summary", path="src/tcp", depth=1)
        by_path = {item["value"]["path"]: item["value"] for item in groups if item["item_type"] == "coverage_group"}
        self.assertEqual(by_path["src/tcp/auth.c"]["distinct_records"], 3)
        self.assertEqual(by_path["src/tcp/queue.c"]["distinct_records"], 2)
        indexes = self.all_items(input_id="coverage_match_summary", view="index", gap_ids=["unknown"])
        self.assertIsNone(indexes[0]["value"]["count"])
        self.assertEqual(indexes[0]["value"]["coverage_state"], "indeterminate")
        page = self.query(input_id="coverage_match_summary", view="summary", path="src/tcp", depth=1)
        self.assertEqual(page["selected_records"], 4)  # groups count the ambiguous record twice

    def test_oversized_record_and_metadata_are_losslessly_paged(self):
        self.gaps[0]["raw"] = "大字段\"\\\n" * 2000
        write_json(self.run / "inputs/coverage-gaps.json", self.gaps)
        before = (self.run / "inputs/coverage-gaps.json").read_bytes()
        records = self.all_items(view="records", max_chars=1000)
        self.assertEqual([item["value"] for item in records], self.gaps)
        self.assertEqual((self.run / "inputs/coverage-gaps.json").read_bytes(), before)
        self.summary["sources"][0]["diagnostic"] = "diagnostic" * 1000
        write_json(self.run / "inputs/coverage-match-summary.json", self.summary)
        items = self.all_items(input_id="coverage_match_summary", view="records", max_chars=1000)
        self.assertEqual(items[-1]["value"]["sources"], self.summary["sources"])

    def test_large_gap_set_has_small_summary_and_complete_id_index(self):
        records = [{**self.gaps[index % 3], "coverage_id": f"gap-{index:04d}",
                    "raw": {"detail": "原始覆盖数据" * 80}} for index in range(1788)]
        file = self.run / "inputs/coverage-gaps.json"
        write_json(file, records)
        before = file.read_bytes()
        summary = self.query(view="summary", path="src", depth=1)
        self.assertIsNone(summary["next_page_token"])
        self.assertEqual(summary["selected_records"], 1788)
        self.assertEqual({item["value"]["path"]: item["value"]["distinct_records"] for item in summary["items"]},
                         {"src/tcp": 1192, "src/tcp2": 596})
        index = self.all_items(view="index", path="src/tcp", max_chars=12000)
        self.assertEqual([item["value"]["coverage_id"] for item in index],
                         [record["coverage_id"] for record in records if record["path"].startswith("src/tcp/")])
        self.assertEqual(file.read_bytes(), before)

    def test_cursor_is_bound_to_query_snapshot_and_worker(self):
        self.gaps[0]["raw"] = "x" * 5000
        file = self.run / "inputs/coverage-gaps.json"
        write_json(file, self.gaps)
        token = self.query(view="records", max_chars=1000)["next_page_token"]
        self.assertIsNotNone(token)
        for kwargs in ({"view": "index"}, {"view": "records", "path": "src/tcp"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.query(page_token=token, **kwargs)
        with self.assertRaises(SourceAccessError):
            coverage_read(str(self.root), self.run_id, self.action, "another-worker")
        self.gaps[0]["raw"] += "changed"
        write_json(file, self.gaps)
        with self.assertRaises(ValueError):
            self.query(view="records", page_token=token)

    def test_legacy_task_and_authorized_input_boundaries(self):
        task = read_json(self.task_path)
        task.pop("planning_policy", None)
        task.pop("planning_io_policy", None)
        write_json(self.task_path, task)
        before = self.task_path.read_bytes()
        self.assertEqual(len(self.all_items(view="records")), len(self.gaps))
        self.assertEqual(self.task_path.read_bytes(), before)
        with self.assertRaises(SourceAccessError):
            self.query(input_id="asset_candidates")
        task["inputs"] = [item for item in task["inputs"] if item["input_id"] != "coverage_gaps"]
        write_json(self.task_path, task)
        with self.assertRaises(SourceAccessError):
            self.query()
        outside = self.root / "outside.json"
        write_json(outside, self.gaps)
        task["inputs"].append({"input_id": "coverage_gaps", "path": str(outside)})
        write_json(self.task_path, task)
        with self.assertRaises(SourceAccessError):
            self.query()

    def test_unmatched_absolute_and_unc_paths_have_usable_drilldown(self):
        self.summary["unmatched"] = [
            {"coverage_id": "absolute", "path": "/vendor/tree/auth.c", "matches": []},
            {"coverage_id": "unc", "path": "\\\\server\\share\\auth.c", "matches": []},
        ]
        write_json(self.run / "inputs/coverage-match-summary.json", self.summary)
        for identifier, expected in (("absolute", ["/vendor", "/vendor/tree", "/vendor/tree/auth.c"]),
                                     ("unc", ["//server", "//server/share", "//server/share/auth.c"])):
            path = None
            for target in expected:
                items = self.all_items(input_id="coverage_match_summary", view="summary", path=path,
                                       gap_ids=[identifier])
                group = next(item["value"] for item in items if item["item_type"] == "coverage_group")
                self.assertEqual(group["path"], target)
                path = group["path"]
            rows = self.all_items(input_id="coverage_match_summary", view="records", path=path)
            self.assertEqual([item["value"]["coverage_id"] for item in rows if item["item_type"] == "coverage_record"],
                             [identifier])


if __name__ == "__main__":
    unittest.main()
