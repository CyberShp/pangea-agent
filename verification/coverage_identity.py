"""Verify coverage identity transport using temporary assets and the real Graph.

No model is invoked. Synthetic notes only advance the protocol fixture to each
consumer; this does not assess analysis quality or measured coverage improvement.
Imports deliberately use the caller's runtime, including a packaged Python .pth.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.assets import asset_detail, import_asset, prepare_asset_extraction
from pangea_agent.cli.adapter_api import bind_action, settle_action
from pangea_agent.cli.run_module_analysis import run_module_analysis, resume_module_analysis
from pangea_agent.cli.source_first_api import input_read, plan_write, result_write, task_open, work_finish
from pangea_agent.documents import coverage as coverage_module
from pangea_agent.documents.coverage import parse_coverage_combined, relevant_zero_coverage


IDENTITY_FIELDS = ("product", "c_version", "b_version")
REQUEST_IDENTITY = {"product": "requested-product", "c_version": "requested-C", "b_version": "requested-B"}
REPORTED_IDENTITY = {"product": "reported-product", "c_version": "reported-C", "b_version": "reported-B"}
FROZEN_FILES = (
    "coverage-diagnostics.json", "coverage-match-summary.json", "asset-snapshots.json",
    "source-manifest.json", "coverage-gaps.json",
)


def combined(identity: dict) -> dict:
    return {
        "status": "success", **deepcopy(identity),
        "query_input": {**REQUEST_IDENTITY, "source": "summary"},
        "scope": {"requested": "module", "recursive": True},
        "snapshot": {"stable": False, "sources": [{"source": "summary", "coverage_timestamp": "synthetic"}]},
        "source_semantics": {"preferred": "summary"},
        "summary": {"per_source": [{"source": "summary"}]},
        "uncovered": {
            "functions": [{"source": "summary", "relative_path": "module/tls.c", "uncovered_functions": ["connect_session"]}],
            "lines": [{"source": "summary", "relative_path": "module/tls.c", "uncovered_lines": [2]}],
            "branches": [{"source": "summary", "relative_path": "module/tls.c", "uncovered_branches": [
                {"line": 2, "block": "0", "branch": "0", "count": 0}]}],
        },
        "indeterminate": {"branches": [{"source": "summary", "relative_path": "module/tls.c", "indeterminate_branches": [
            {"line": 2, "block": "0", "branch": "1", "count": "-"}]}]},
    }


class CoverageIdentityTransport(unittest.TestCase):
    def assert_identity(self, actual: dict, expected: dict) -> None:
        self.assertEqual({key: actual[key] for key in IDENTITY_FIELDS if key in actual}, expected)
        self.assertEqual(actual["query_input"], {**REQUEST_IDENTITY, "source": "summary"})

    def test_report_identity_changes_are_observable_without_changing_gap_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "combined.json"
            write_json(source, combined(REPORTED_IDENTITY))
            first = parse_coverage_combined(source)
            self.assert_identity(first["acquisition"], REPORTED_IDENTITY)
            for field in IDENTITY_FIELDS:
                with self.subTest(field=field):
                    changed = {**REPORTED_IDENTITY, field: "  changed-" + field + "-版本  "}
                    write_json(source, combined(changed))
                    result = parse_coverage_combined(source)
                    self.assert_identity(result["acquisition"], changed)
                    self.assertNotEqual(first["acquisition"], result["acquisition"])
                    self.assertEqual(first["records"], result["records"])
                    self.assertEqual(first["warnings"], result["warnings"])
            self.assertEqual(len(first["records"]), 4)
            self.assertEqual(len(relevant_zero_coverage({"matched": first["records"]})), 3)
            unknown = [record for record in first["records"] if record["coverage_state"] == "indeterminate"]
            self.assertEqual(len(unknown), 1)
            self.assertIsNone(unknown[0]["count"])

    def test_absent_null_and_empty_report_fields_never_fall_back_to_request(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "combined.json"
            for identity in ({}, {key: None for key in IDENTITY_FIELDS},
                             {key: "" for key in IDENTITY_FIELDS}, {"product": None, "c_version": ""}):
                with self.subTest(identity=identity):
                    write_json(source, combined(identity))
                    self.assert_identity(parse_coverage_combined(source)["acquisition"], identity)

    def fixture(self, root: Path, scenario: str, identity: dict):
        data = root / "data"
        repo = data / "repositories/sample/module"
        repo.mkdir(parents=True)
        (repo / "tls.c").write_text(
            "int connect_session(int ready) {\n    if (!ready) return -1;\n    return 0;\n}\n", encoding="utf-8")
        source = root / "combined.json"
        write_json(source, combined(identity))
        asset = import_asset(str(data), str(source), "coverage", "Synthetic identity transport fixture")
        self.assertEqual(Path(asset.source_path).read_bytes(), source.read_bytes())
        prepare_asset_extraction(str(data), asset.asset_id)
        detail = asset_detail(str(data), asset.asset_id)
        self.assert_identity(detail["result"]["acquisition"], identity)
        request = root / "request.json"
        write_json(request, {
            "data_root": str(data), "repository": "sample", "target": "Synthetic coverage identity transport",
            "workflow_version": "source-first-v1", "analysis_profile": "behavior-test-v2",
            "analysis_settings": {"scenario": scenario, "mode": "depth", "objective": "interface-contract"},
            "source_scope": ["module"], "asset_ids": [asset.asset_id],
            "asset_revisions": {asset.asset_id: detail["asset"]["input_revision"]},
        })
        response = run_module_analysis(str(request))
        return data, data / "runs" / response["run_id"], asset, response

    def read_input(self, binding: tuple[str, str, str, str], input_id: str):
        chunks, cursor = [], None
        while True:
            page = input_read(*binding, input_id=input_id, cursor=cursor, max_chars=512)
            chunks.append(page["text"])
            cursor = page["next_cursor"]
            if not cursor:
                return json.loads("".join(chunks))

    def bind_and_read(self, response: dict, role: str, session: str, identity: dict):
        actions = [action for action in response["agent_actions"] if action["role"] == role]
        self.assertEqual(len(actions), 1, response)
        action = actions[0]
        self.assertTrue(action["action_id"].startswith(response["run_id"] + ":"))
        if action.get("task_id"):
            self.assertEqual(action["task_id"], session)
        binding = (response["data_root"], response["run_id"], action["action_id"], session)
        bind_action(*binding)
        task = task_open(*binding)["task"]
        declared = task.get("inputs")
        if declared is None:
            self.assertEqual(task["deferred_fields"]["inputs"]["input_id"], "task:inputs")
            declared = self.read_input(binding, "task:inputs")
        self.assertTrue({"coverage_diagnostics", "coverage_match_summary"}.issubset(
            {item["input_id"] for item in declared}))
        diagnostics = self.read_input(binding, "coverage_diagnostics")
        summary = self.read_input(binding, "coverage_match_summary")
        self.assertEqual(len(diagnostics), 1)
        self.assert_identity(diagnostics[0], identity)
        self.assert_identity(summary["sources"][0], identity)
        self.assertEqual(len(summary["matched"]), 4)
        unknown = [record for record in summary["matched"] if record["coverage_state"] == "indeterminate"]
        self.assertEqual(len(unknown), 1)
        self.assertIsNone(unknown[0]["count"])
        return binding, task

    def finish(self, binding: tuple[str, str, str, str], revision: int):
        work_finish(*binding, revision=revision)
        response = settle_action(*binding[:3])
        self.assertNotIn("validation", response, response)
        # Every next identity is consumed from the Graph response, never guessed
        # from historical directories, action names or worker output.
        self.assertEqual(response["run_id"], binding[1])
        return response

    def snapshot(self, run: Path):
        return {name: (run / "inputs" / name).read_bytes() for name in FROZEN_FILES}

    def test_report_and_request_identity_reach_all_roles_and_remain_frozen(self):
        for scenario in ("branch-analysis", "coverage-analysis"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                data, run, asset, response = self.fixture(Path(directory), scenario, REPORTED_IDENTITY)
                frozen = self.snapshot(run)
                snapshots = read_json(run / "inputs/asset-snapshots.json")
                self.assert_identity(snapshots[0]["result"]["acquisition"], REPORTED_IDENTITY)
                manifest = read_json(run / "inputs/source-manifest.json")
                self.assert_identity(manifest["coverage_diagnostics"]["acquisitions"][0], REPORTED_IDENTITY)
                self.assertEqual(len(read_json(run / "inputs/coverage-gaps.json")), 3)

                # Reparse this test's asset after the Run has frozen it. A new
                # asset revision must not rewrite or retarget existing tasks.
                original_revision = asset_detail(str(data), asset.asset_id)["asset"]["input_revision"]
                changed_identity = {key: "later-" + value for key, value in REPORTED_IDENTITY.items()}
                write_json(Path(asset.source_path), combined(changed_identity))
                prepare_asset_extraction(str(data), asset.asset_id)
                changed = asset_detail(str(data), asset.asset_id)
                self.assert_identity(changed["result"]["acquisition"], changed_identity)
                self.assertNotEqual(changed["asset"]["input_revision"], original_revision)
                response = resume_module_analysis(response["run_id"], str(data))
                self.assertEqual(self.snapshot(run), frozen)

                binding, _ = self.bind_and_read(response, "planning", "synthetic-planner", REPORTED_IDENTITY)
                saved = plan_write(*binding, expected_revision=0, unit={
                    "title": "Synthetic connection", "purpose": "Identity transport protocol fixture only",
                    "owned_files": [{"repo_id": "sample", "path": "module/tls.c"}],
                })
                response = self.finish(binding, saved["revision"])
                binding, _ = self.bind_and_read(response, "analysis", "synthetic-analysis", REPORTED_IDENTITY)
                saved = result_write(*binding, expected_revision=0, records=[{
                    "kind": "note", "body": "Synthetic transport fixture; no model or business conclusion."}])
                response = self.finish(binding, saved["revision"])
                binding, task = self.bind_and_read(response, "review", "synthetic-reviewer", REPORTED_IDENTITY)
                self.assertEqual(task["review_stage"], "independent_review")
                saved = result_write(*binding, expected_revision=0, records=[{
                    "kind": "note", "body": "Synthetic independent-review transport fixture; no quality verdict."}])
                response = self.finish(binding, saved["revision"])
                binding, task = self.bind_and_read(response, "review", "synthetic-reviewer", REPORTED_IDENTITY)
                self.assertEqual(task["review_stage"], "comparison_review")
                resume_module_analysis(response["run_id"], str(data))
                self.assertEqual(self.snapshot(run), frozen)
                self.assert_identity(self.read_input(binding, "coverage_diagnostics")[0], REPORTED_IDENTITY)
                # The comparison task deliberately stays unfinished: transport
                # evidence is not a successful business acceptance verdict.

    def test_resume_does_not_backfill_an_existing_run_with_newly_available_report_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            data, run, asset, response = self.fixture(Path(directory), "coverage-analysis", {})
            frozen = self.snapshot(run)
            write_json(Path(asset.source_path), combined(REPORTED_IDENTITY))
            prepare_asset_extraction(str(data), asset.asset_id)
            self.assert_identity(asset_detail(str(data), asset.asset_id)["result"]["acquisition"], REPORTED_IDENTITY)
            response = resume_module_analysis(response["run_id"], str(data))
            self.bind_and_read(response, "planning", "synthetic-old-planner", {})
            self.assertEqual(self.snapshot(run), frozen)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-runtime-root", type=Path)
    options = parser.parse_args()
    module_file = Path(coverage_module.__file__).resolve()
    if options.expected_runtime_root:
        module_file.relative_to(options.expected_runtime_root.resolve())
    print(json.dumps({"coverage_module": str(module_file), "model_dispatched": False}, ensure_ascii=False), flush=True)
    outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CoverageIdentityTransport))
    print(json.dumps({"status": "passed" if outcome.wasSuccessful() else "failed", "tests": outcome.testsRun,
                      "roles": ["planning", "analysis", "independent_review", "comparison_review"],
                      "old_run_freeze": "passed" if outcome.wasSuccessful() else "see_test_results",
                      "business_acceptance": "not_run"}, ensure_ascii=False), flush=True)
    sys.exit(0 if outcome.wasSuccessful() else 1)
