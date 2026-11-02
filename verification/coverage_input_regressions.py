"""Coverage input regressions: python verification/coverage_input_regressions.py."""
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

from pangea_agent.documents.coverage import (
    match_coverage_records, parse_coverage_xlsx, relevant_zero_coverage,
)


class CoverageInputChecks(unittest.TestCase):
    headers = ["module", "path", "function", "count", "branch_id", "condition", "true_count", "false_count"]

    def parse(self, sheets):
        with tempfile.TemporaryDirectory(prefix="pangea-coverage-input-") as directory:
            path = Path(directory) / "coverage.xlsx"
            workbook = Workbook()
            workbook.remove(workbook.active)
            for title, rows in sheets.items():
                sheet = workbook.create_sheet(title)
                for row in rows:
                    sheet.append(row)
            workbook.save(path)
            workbook.close()
            return parse_coverage_xlsx(path)

    @staticmethod
    def file(path, repo="repo", symbol="run", line_count=20):
        return {"repo_id": repo, "path": path, "line_count": line_count,
                "functions": [{"symbol": symbol, "line": 2}]}

    def test_mixed_table_preserves_both_records_and_blank_rows(self):
        records, warnings = self.parse({"mixed": [self.headers,
            ["src", "src/a.c", "run", 3, "B1", "ready", 3, 0],
            [None] * 8,
            ["src", "src/a.c", "run", 0, "B2", "ready", 0, 0]]})
        self.assertEqual(warnings, [])
        self.assertEqual([(r["coverage_type"], r["row"], r["count"]) for r in records],
                         [("function", 2, 3), ("branch", 2, 3), ("function", 4, 0), ("branch", 4, 0)])
        self.assertEqual(records[1]["branch_id"], "B1")
        self.assertEqual(records[1]["condition"], "ready")

    def test_mixed_table_accepts_rows_with_only_one_record_type(self):
        records, warnings = self.parse({"mixed": [self.headers,
            [None, "src/a.c", "run", None, "B1", "ready", 3, 0],
            ["src", "src/a.c", "run", 3, None, None, None, None]]})
        self.assertEqual(warnings, [])
        self.assertEqual([(r["coverage_type"], r["row"]) for r in records],
                         [("branch", 2), ("function", 3)])

    def test_missing_function_count_is_still_reported_when_not_branch_only(self):
        records, warnings = self.parse({
            "functions": [["module", "path", "function", "count"],
                          [None, "src/a.c", "run", None]],
            "mixed": [self.headers,
                      ["src", "src/a.c", "run", None, "B1", "ready", 3, 0]],
        })
        self.assertEqual([r["coverage_type"] for r in records], ["branch"])
        self.assertEqual(len(warnings), 2)
        self.assertTrue(all("invalid coverage count None" in warning for warning in warnings))

    def test_invalid_function_count_does_not_drop_valid_branch(self):
        records, warnings = self.parse({"mixed": [self.headers,
            ["src", "src/a.c", "run", "bad", "B1", "ready", 3, 0]]})
        self.assertEqual([r["coverage_type"] for r in records], ["branch"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("sheet mixed row 2: invalid coverage count", warnings[0])

    def test_invalid_branch_count_does_not_drop_valid_function(self):
        for counts in [("bad", 0), (0, "bad"), (None, None)]:
            with self.subTest(counts=counts):
                records, warnings = self.parse({"mixed": [self.headers,
                    ["src", "src/a.c", "run", 0, "B1", "ready", *counts]]})
                self.assertEqual([r["coverage_type"] for r in records], ["function"])
                self.assertEqual(len(warnings), 1)
                self.assertIn("sheet mixed row 2: invalid branch counts", warnings[0])

    def test_independent_function_and_branch_sheets_remain_supported(self):
        records, warnings = self.parse({
            "functions": [["module", "function", "count"], ["src", "run", 0]],
            "branches": [["path", "function", "branch_id", "condition", "true_count", "false_count"],
                         ["src/a.c", "run", "B1", "ready", 0, 4]],
        })
        self.assertEqual(warnings, [])
        self.assertEqual([r["coverage_type"] for r in records], ["function", "branch"])
        self.assertEqual(records[0]["path"], "")
        self.assertEqual(records[1]["count"], 4)

    def test_incomplete_columns_do_not_drop_other_record_type(self):
        for missing, kept, warning in [("module", "branch", "function"),
                                       ("false_count", "function", "branch")]:
            with self.subTest(missing=missing):
                index = self.headers.index(missing)
                row = ["src", "src/a.c", "run", 0, "B1", "ready", 0, 4]
                records, warnings = self.parse({"mixed": [
                    self.headers[:index] + self.headers[index + 1:], row[:index] + row[index + 1:]]})
                self.assertEqual([r["coverage_type"] for r in records], [kept])
                self.assertEqual(len(warnings), 1)
                self.assertIn(f"incomplete {warning} coverage columns", warnings[0])

    def record_variants(self, path):
        return [
            {"coverage_type": "function", "path": path, "function": "run", "count": 0},
            {"coverage_type": "branch", "path": path, "function": "run", "count": 4,
             "true_count": 0, "false_count": 4},
            {"coverage_type": "line", "path": path, "line": 2, "count": 0},
            {"coverage_type": "branch", "path": path, "line": 2, "count": 0},
        ]

    def test_exact_path_wins_over_suffix_for_every_record_type(self):
        inventory = {"files": [self.file("vendor/src/a.c"), self.file("src/a.c")]}
        for record in self.record_variants("src\\a.c"):
            with self.subTest(record=record):
                report = match_coverage_records([record], inventory)
                self.assertEqual(report["ambiguous"], [])
                self.assertEqual(report["matched"][0]["matches"][0]["path"], "src/a.c")

    def test_duplicate_exact_paths_across_repositories_remain_ambiguous(self):
        inventory = {"files": [self.file("src/a.c", "one"), self.file("src/a.c", "two"),
                               self.file("vendor/src/a.c", "three")]}
        for record in self.record_variants("src/a.c"):
            with self.subTest(record=record):
                report = match_coverage_records([record], inventory)
                self.assertEqual(report["matched"], [])
                self.assertEqual({m["repo_id"] for m in report["ambiguous"][0]["matches"]}, {"one", "two"})
                self.assertEqual(relevant_zero_coverage(report), [])

    def test_suffix_fallback_is_retained_in_both_directions(self):
        for requested, actual in [("src/a.c", "vendor/src/a.c"), ("checkout/src/a.c", "src/a.c")]:
            for record in self.record_variants(requested):
                with self.subTest(requested=requested, record=record):
                    report = match_coverage_records([record], {"files": [self.file(actual)]})
                    self.assertEqual(report["matched"][0]["matches"][0]["path"], actual)

    def test_multiple_suffix_candidates_remain_ambiguous(self):
        report = match_coverage_records(self.record_variants("a.c"),
            {"files": [self.file("src/a.c"), self.file("vendor/a.c")]})
        self.assertEqual(len(report["ambiguous"]), 4)
        self.assertEqual(report["matched"], [])

    def test_exact_file_does_not_fall_through_when_symbol_or_line_absent(self):
        inventory = {"files": [self.file("src/a.c", symbol="other", line_count=1),
                               self.file("vendor/src/a.c")]}
        report = match_coverage_records(self.record_variants("src/a.c"), inventory)
        self.assertEqual(len(report["unmatched"]), 4)
        self.assertEqual(report["matched"], [])

    def test_no_path_uses_existing_symbol_matching_not_module_guessing(self):
        record = {"function": "run", "module": "unrelated", "count": 0}
        report = match_coverage_records([record], {"files": [self.file("src/a.c")]})
        self.assertEqual(len(report["matched"]), 1)
        report = match_coverage_records([record], {"files": [self.file("src/a.c"), self.file("other/a.c")]})
        self.assertEqual(len(report["ambiguous"]), 1)

    def test_covered_excluded_and_partial_branch_retained_as_gap(self):
        records, warnings = self.parse({"mixed": [self.headers,
            ["src", "src/a.c", "run", 4, "covered", "ready", 2, 2],
            ["src", "src/a.c", "run", 4, "partial", "ready", 4, 0]]})
        self.assertEqual(warnings, [])
        report = match_coverage_records(records, {"files": [self.file("src/a.c")]})
        gaps = relevant_zero_coverage(report)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["branch_id"], "partial")


if __name__ == "__main__":
    unittest.main()
