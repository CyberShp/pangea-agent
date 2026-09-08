"""Offline regression checks. All fixtures are synthetic; no internal query data."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pangea_agent.documents.coverage_input import coverage_page, gap_records, prepare_coverage, read_input
from pangea_agent.skill_runs import create_skill_run, coverage_operation


def fixture():
    return {"status": "partial", "sources": [
        {"source": "auto", "branches": {"total": 4, "covered": 2, "rate": .5}},
        {"source": "summary", "branches": {"total": 2, "covered": 1, "rate": .5}}],
        "uncovered_functions": [{"source": "auto", "file_path": file, "uncovered_functions": ["same"]} for file in ("a.c", "b.c")],
        "uncovered_branches": [{"source": source, "file_path": file, "uncovered_branches": [
            {"line": 1, "block": "0", "branch": "0", "count": "0"},
            {"line": 1, "block": "0", "branch": "1", "count": "-"}]} for source, file in (("auto", "a.c"), ("summary", "a.c"), ("auto", "b.c"))],
        "uncovered_lines": [], "missing": ["synthetic-source"], "warnings": ["synthetic incomplete source"]}


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_identity_unknown_and_paging(self):
        folder = self.root / "inputs/coverage"
        folder.mkdir(parents=True)
        (folder / "combined.json").write_text(json.dumps(fixture()), encoding="utf-8-sig")
        first = coverage_page(self.root, limit=2)
        self.assertEqual(first["total"], 5)
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["next_cursor"], 2)
        last = coverage_page(self.root, cursor=4, limit=2)
        self.assertIsNone(last["next_cursor"])
        self.assertEqual(len(coverage_page(self.root, source="summary")["items"]), 1)
        self.assertEqual(len({g["gap_id"] for g in gap_records(fixture())}), 5)

    def test_table_counts_not_percentages(self):
        file = self.root / "input.csv"
        file.write_text("source,file_path,kind,count,function\nauto,a.c,function,0,same\nauto,b.c,function,-,same\nauto,c.c,function,1,same\n", encoding="utf-8-sig")
        data = read_input(file)
        self.assertEqual(len(gap_records(data)), 1)
        self.assertEqual(len(data["unknown_records"]), 1)
        file.write_text("source,file_path,kind,count,function\nauto,a.c,function,20%,same\n")
        with self.assertRaisesRegex(ValueError, "百分比"):
            read_input(file)

    def test_optional_scope_and_explicit_append(self):
        repo = self.root / "repositories/repo"
        repo.mkdir(parents=True)
        (repo / "a.c").write_text("int same(void) { return 0; }")
        (repo / "b.c").write_text("int other(void) { return 1; }")
        report = self.root / "coverage.json"
        report.write_text(json.dumps(fixture()))
        request = self.root / "request.json"
        request.write_text(json.dumps({"request_version": "2.0", "data_root": str(self.root), "repository": "repo", "target": "synthetic",
            "scenario": "coverage-analysis", "mode": "speed", "source_scope": [], "coverage_input": {"kind": "file", "path": str(report)}}))
        run = create_skill_run(str(request))
        self.assertEqual(run["skill"]["skill_id"], "codetalks-coverage-skill")
        self.assertEqual(run["source_snapshot_status"], "pending")
        run_root = Path(run["run_root"])
        self.assertFalse((run_root / "inputs/source").exists())
        opts = (str(self.root), run["run_id"])
        self.assertEqual(coverage_operation(*opts, "coverage-prepare")["total"], 5)
        coverage_operation(*opts, "prepare-source", scope=["a.c"])
        (repo / "a.c").write_text("changed")
        coverage_operation(*opts, "prepare-source", scope=["a.c", "b.c"])
        self.assertNotEqual((run_root / "inputs/source/repository/a.c").read_text(), "changed")
        self.assertTrue((run_root / "inputs/source/repository/b.c").exists())
        with self.assertRaises(ValueError):
            coverage_operation(*opts, "prepare-source", scope=["../coverage.json"])

    def test_combined_once_and_version_literal(self):
        folder = self.root / "inputs/coverage"
        folder.mkdir(parents=True)
        skill = self.root / "query/scripts"
        skill.mkdir(parents=True)
        # This local synthetic process records its exact argv, not business data.
        (skill / "coverage_query.py").write_text("import sys,json\nfrom pathlib import Path\np=Path(__file__).with_name('calls.json')\nassert not p.exists()\np.write_text(json.dumps(sys.argv))\nprint(" + repr(json.dumps(fixture())) + ")\n")
        (folder / "input.json").write_text(json.dumps({"kind": "query", "skill_root": str(skill.parent), "query": {"product": "P", "c_version": "  VERSION A  ", "module": "M"}}))
        prepare_coverage(self.root)
        prepare_coverage(self.root)
        args = json.loads((skill / "calls.json").read_text())
        self.assertEqual(args[args.index("--version") + 1], "  VERSION A  ")


if __name__ == "__main__":
    unittest.main()
