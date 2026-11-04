"""Offline review presentation regressions; no model or external service needed."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pangea_agent.report.source_first import (  # noqa: E402
    _behavior_markdown, _html, _record_markdown, _record_title, _review_markdown,
    write_source_first_reports,
)
from pangea_agent.analysis_scenarios import freeze_scene


def record(kind, body, identifier="review", supersedes=None):
    return SimpleNamespace(kind=kind, body=body, record_id=identifier,
                           supersedes=supersedes or [], evidence=[], relates_to=[])


def displayed(markdown):
    return markdown.split("<details><summary>查看原始复核记录</summary>", 1)[0]


class ReviewReportTests(unittest.TestCase):
    def render_report(self, mode, policy, profile="behavior-test-v2"):
        with tempfile.TemporaryDirectory() as directory:
            state = {"data_root": directory, "run_id": "review-report",
                     "task_contract": {"analysis_profile": profile,
                                       "analysis_settings": {"mode": mode, "scenario": "module-analysis"}}}
            freeze_scene(Path(directory) / "runs" / state["run_id"], state["task_contract"])
            progress = {"run_id": "review-report", "lifecycle_status": "complete",
                        "quality_status": "UNRESOLVED" if policy == "disabled" else "PASS"}
            if policy is not None:
                progress["review_policy"] = policy
            with patch("pangea_agent.flow_stability.publish_flow_baseline", return_value=None):
                paths = write_source_first_reports(state, progress=progress)
            return {key: Path(path).read_text(encoding="utf-8") for key, path in paths.items()}

    def test_disabled_review_report_is_truthful_in_both_modes_and_profiles(self):
        for profile in ("behavior-test-v1", "behavior-test-v2"):
            for mode in ("depth", "speed"):
                with self.subTest(profile=profile, mode=mode):
                    for report in self.render_report(mode, "disabled", profile).values():
                        self.assertIn("复核模式：已停用：未执行模型审核，直接交付分析结果", report)
                        self.assertIn("分析已完成并交付；未执行模型审核", report)
                        self.assertIn("UNRESOLVED 表示未作审核质量裁决", report)
                        for misleading in ("直接审核首轮结果", "独立盲审后对照复核", "用例分析与审核已完成"):
                            self.assertNotIn(misleading, report)

    def test_enabled_review_report_preserves_historical_mode_labels(self):
        for policy in (None, "enabled"):
            for mode, label in (("depth", "标准型：独立盲审后对照复核"),
                                ("speed", "速度型：直接审核首轮结果，未执行独立盲审")):
                with self.subTest(policy=policy, mode=mode):
                    for report in self.render_report(mode, policy).values():
                        self.assertIn("复核模式：" + label, report)
                        self.assertIn("用例分析与审核已完成", report)
                        self.assertNotIn("复核模式：已停用", report)

    def test_decision_fields_localized_without_translating_prose(self):
        body = {"summary": "No additional evidence is available.\nKeep the original assessment.",
                "disposition": "unresolved", "version_set_id": "version-set-123",
                "correction_record_ids": [], "basis": "pass", "conclusion": "finding"}
        original = copy.deepcopy(body)
        markdown = _record_markdown(record("review_decision", body))
        visible = displayed(markdown)
        self.assertIn("##### 复核裁决\n\n待确认", visible)
        self.assertIn("##### 本次对照版本", visible)
        self.assertIn("##### 需修正的记录\n\n```text\n[]\n```", visible)
        self.assertIn("##### 依据\n\npass", visible)
        self.assertIn("##### 结论\n\nfinding", visible)
        self.assertIn(body["summary"], visible)
        self.assertNotIn("无问题", visible)
        self.assertEqual(body, original)
        self.assertLess(visible.index("复核裁决"), visible.index("复核摘要"))
        self.assertIn('"disposition": "unresolved"', markdown)

    def test_nested_decisions_remain_separate_and_unknown_fields_survive(self):
        body = {"decisions": [
            {"finding_record_id": "rec-a", "verdict": "confirmed", "conclusion": "Original A"},
            {"finding_record_id": "rec-b", "verdict": "refuted", "basis": "Original B"}],
            "future_unrecognized_key": {"status": "future-state", "opaque_prose": "unresolved"},
            "unresolved": ["one"], "unresolved_items": ["two"]}
        visible = displayed(_review_markdown(body))
        for expected in ("对照裁决", "第 1 项", "第 2 项", "已确认", "已反证", "Original A", "Original B",
                         "future_unrecognized_key", "future-state", "opaque_prose", "unresolved", "one", "two"):
            self.assertIn(expected, visible)
        self.assertNotIn('{"finding_record_id"', visible)
        self.assertEqual(visible.count("##### 待确认事项"), 2)

    def test_only_enum_fields_translate_even_when_text_is_an_enum_word(self):
        body = {"status": "PASS", "review_stage": "comparison_review", "review_mode": "speed",
                "summary": "pass", "reason": "unresolved", "record_id": "finding",
                "snippet": "status=pass\n```text\nunresolved", "source_evidence": ["pass"]}
        visible = displayed(_review_markdown(body))
        self.assertIn("##### 状态\n\n通过", visible)
        self.assertIn("##### 复核阶段\n\n对照复核", visible)
        self.assertIn("##### 复核模式\n\n速度型", visible)
        self.assertIn("##### 复核摘要\n\npass", visible)
        self.assertIn("##### 原样引文\n\n````text\n" + body["snippet"], visible)
        self.assertIn("```text\nfinding\n```", visible)
        self.assertIn('```text\n[\n  "pass"\n]\n```', visible)

    def test_literal_object_and_array_subtrees_are_not_projected_as_review_fields(self):
        evidence = {"status": "pass", "disposition": "finding",
                    "nested": [{"verdict": "refuted", "review_mode": "speed"}]}
        body = {"disposition": "unresolved", "source_evidence": [evidence],
                "snippet": evidence, "ref": {"status": "confirmed"},
                "correction_record_ids": [{"status": "pending", "record_id": "r-1"}]}
        original = copy.deepcopy(body)
        visible = displayed(_review_markdown(body))
        self.assertIn("##### 复核裁决\n\n待确认", visible)
        for key in ("source_evidence", "snippet", "ref", "correction_record_ids"):
            raw = json.dumps(body[key], ensure_ascii=False, indent=2, sort_keys=True)
            self.assertIn("```text\n" + raw + "\n```", visible)
        for translated in ("需定向修正", "已反证", "速度型", "已确认", "待处理"):
            self.assertNotIn(translated, visible)
        self.assertEqual(body, original)

    def test_json_strings_fences_and_arrays_are_readable(self):
        for body in ('{"disposition":"pass","summary":"English remains English"}',
                     '```json\n{"disposition":"finding"}\n```',
                     '[{"status":"uncertain"},{"status":"covered"},{"status":"missed"}]'):
            with self.subTest(body=body):
                result = _record_markdown(record("review_decision", body))
                self.assertIn("查看原始复核记录", result)
                self.assertIn(body, result)
                self.assertNotIn("{\"disposition\"", displayed(result))
        self.assertIn("需定向修正", displayed(_review_markdown('```json\n{"disposition":"finding"}\n```')))

    def test_malformed_or_non_structured_records_preserve_original_text(self):
        for body in ('{"disposition": broken', "English review with no structured fields.", None,
                     [], {}, [False, 0, None, "pending"], {"status": {"unexpected": 3}}):
            with self.subTest(body=body):
                result = _record_markdown(record("review_decision", body))
                self.assertIn("查看原始复核记录", result)
                expected = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True)
                self.assertIn(expected, result)
        self.assertEqual(_record_title(record("review_decision", '```json\n{}\n```')), "复核结论")

    def test_non_review_records_keep_their_existing_renderer(self):
        item = record("note", {"disposition": "pass", "summary": "unresolved"})
        result = _record_markdown(item)
        self.assertNotIn("查看原始复核记录", result)
        self.assertNotIn("复核裁决", result)
        projected = _record_markdown(item, review_context=True)
        self.assertIn("复核裁决", projected)
        self.assertIn("##### 复核摘要\n\nunresolved", projected)

    def test_rendered_html_escapes_sources_and_preserves_fence_content(self):
        body = {"disposition": "finding", "summary": "<script>alert(1)</script>",
                "snippet": '```\n<img src=x onerror="bad()">\n```',
                "basis": {"status": "agree", "reason": "English rationale"}}
        html = _html(_record_markdown(record("review_decision", body)))
        self.assertIn("复核裁决", html)
        self.assertIn("需定向修正", html)
        self.assertIn("同意", html)
        self.assertIn("English rationale", html)
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("```", html)

    def test_accepted_review_section_uses_active_records_and_context(self):
        with tempfile.TemporaryDirectory() as directory:
            state = {"data_root": directory, "run_id": "review-report"}
            old = record("review_decision", {"disposition": "pass", "summary": "RETIRED"}, "old")
            current = record("review_decision", {"disposition": "finding", "summary": "修正两个预期"}, "new", ["old"])
            note = record("note", {"basis": "Original detailed evidence", "open_issues": []}, "note")
            result = SimpleNamespace(records=[old, current, note], warnings=[])
            action = {"stage": "comparison_review", "status": "accepted", "task": {"unit_id": "u"}}
            markdown = _behavior_markdown(state, {}, [(action, result)])
            self.assertIn("业务用例质量复核", markdown)
            self.assertIn("需定向修正", markdown)
            self.assertIn("复核说明", markdown)
            self.assertIn("未解决事项", markdown)
            self.assertIn("Original detailed evidence", markdown)
            self.assertNotIn("RETIRED", markdown)
            self.assertNotIn("复核裁决\n\n通过", markdown)


if __name__ == "__main__":
    unittest.main(verbosity=2)
