"""Verify same-record diagram facts, old freezes and portable reports without a model.

Run: python verification/native_diagram_records.py
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pangea_agent.agent_io import write_json
from pangea_agent.analysis_scenarios import freeze_scene, scene_spec
from pangea_agent.flow_schema import check_flow_structure, normalize_flow
from pangea_agent.graph.result_store import active_records, append_records, declare_completion, initialize_result, read_result
from pangea_agent.models.source_first import SourceBinding
from pangea_agent.report.flow_section import flow_section_markdown
from pangea_agent.report.native_diagrams import native_diagram_sections
from pangea_agent.report.source_first import _html


ROOT = Path(__file__).resolve().parents[1]
RUBRICS = ROOT / "src/pangea_agent/rubrics/builtin"


def flow() -> dict:
    rubric = (RUBRICS / "scene_flow.md").read_text(encoding="utf-8")
    return json.loads(re.search(r"```json\s*\n(.*?)\n```", rubric, re.S).group(1))


def report(records) -> tuple[str, str]:
    assets = {}
    markdown = "\n".join(flow_section_markdown([("unit", list(records))], diagram_assets=assets,
                                            reference=lambda value, unit, key: str(value),
                                            other_record_markdown=lambda unit, record: []))
    return markdown, _html(markdown, assets)


class NativeDiagramRecords(unittest.TestCase):
    def test_one_result_record_preserves_all_three_views_in_storage_normalization_and_report(self):
        body = flow()
        self.assertEqual(check_flow_structure(body), [])
        with tempfile.TemporaryDirectory(prefix="pangea-native-diagrams-") as folder:
            target = Path(folder) / "result.json"
            binding = SourceBinding(run_id="run", data_root=folder, action_id="run:analysis:unit", task_id="same-worker")
            initialize_result(target, binding)
            first = append_records(target, binding, 0, [{"kind": "flow", "body": body}])
            self.assertIsNone(read_result(target).completion)
            stored = read_result(target)
            self.assertEqual(len(stored.records), 1)
            self.assertEqual(stored.records[0].body, body)
            normalized, _ = normalize_flow(stored.records[0].body)
            self.assertEqual(normalized[0]["sequence"], body["sequence"])
            self.assertEqual(normalized[0]["call_graph"], body["call_graph"])
            markdown, page = report(active_records(stored))
            for value in ("时序交互", "函数与变量关系", "凭据校验结果", "密码不匹配", "req->user", "web:src/login.c:35", "部分数据"):
                self.assertIn(value, markdown)
            self.assertIn("互斥分支", page)
            self.assertIn("check_password", page)
            self.assertIn("req-&gt;user", page)
            self.assertNotIn("req-&amp;gt;user", page)
            self.assertIn('"format_version": "sequence-v1"', markdown)
            self.assertIn('"status": "partial"', markdown)
            final = declare_completion(target, binding, first["revision"], complete=True)
            self.assertTrue(final["complete"])
            self.assertEqual(read_result(target).binding.task_id, "same-worker")

    def test_supersession_keeps_exact_previous_facts_and_uses_current_record_in_report(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "result.json"
            binding = SourceBinding(run_id="run", data_root=folder, action_id="run:analysis:unit", task_id="same-worker")
            initialize_result(target, binding)
            old = flow()
            old["sequence"]["messages"][0]["label"] = "RETIRED-INTERACTION"
            first = append_records(target, binding, 0, [{"kind": "flow", "body": old}])
            current = flow()
            second = append_records(target, binding, first["revision"], [{"kind": "flow", "body": current, "supersedes": first["record_ids"]}])
            stored = read_result(target)
            self.assertEqual(stored.records[0].body, old)
            self.assertEqual(stored.records[1].body, current)
            self.assertNotIn("RETIRED-INTERACTION", report(active_records(stored))[0])
            self.assertEqual(len(list(active_records(stored))), 1)
            self.assertEqual(second["revision"], 2)

    def test_missing_and_malformed_optional_facts_do_not_block_flow_or_claim_success(self):
        for value in (None, "raw non-object", [], {"status": ["unexpected"], "participants": "bad", "messages": [None, {"from": "missing", "to": {"id": "not-string"}}], "fragments": [{"operands": [None]}]}):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as folder:
                body = flow()
                body["sequence"] = deepcopy(value)
                body["call_graph"] = deepcopy(value)
                self.assertEqual(check_flow_structure(body), [])
                target = Path(folder) / "result.json"
                binding = SourceBinding(run_id="run", data_root=folder, action_id="run:analysis:unit", task_id="worker")
                initialize_result(target, binding)
                first = append_records(target, binding, 0, [{"kind": "flow", "body": body}])
                stored = read_result(target)
                self.assertEqual(stored.records[0].body, body)
                markdown, _ = report(stored.records)
                self.assertNotIn("Agent 已声明就绪", markdown)
                self.assertTrue(declare_completion(target, binding, first["revision"], complete=True)["complete"])

    def test_unknown_references_remain_visible_without_mutating_source(self):
        body = flow()
        body["sequence"]["messages"][0]["from"] = "unknown-sender"
        body["sequence"]["fragments"][0]["operands"][0]["message_ids"].append("unknown-message")
        body["call_graph"]["calls"][0]["to"] = "unknown-callee"
        body["call_graph"]["variables"][0]["function"] = "unknown-owner"
        before = deepcopy(body)
        text = "\n".join(native_diagram_sections(body))
        for value in ("unknown-sender", "unknown-message", "unknown-callee", "unknown-owner", "原引用保留"):
            self.assertIn(value, text)
        self.assertEqual(body, before)

    def test_report_escapes_content_and_keeps_unrecognized_fields(self):
        body = flow()
        body["sequence"]["messages"][0]["label"] = '<script>alert("x")</script>|保留完整说明'
        body["call_graph"]["variables"][0]["extra"] = "unrecognized-source-fact"
        markdown = "\n".join(native_diagram_sections(body))
        page = _html(markdown)
        self.assertNotIn('<script>alert("x")</script>', page)
        self.assertIn("保留完整说明", page)
        self.assertIn("unrecognized-source-fact", page)
        self.assertIn("不表示已验证运行轨迹", page)

    def test_old_flow_and_frozen_rubric_are_not_upgraded_or_backfilled(self):
        legacy = flow()
        legacy.pop("sequence")
        legacy.pop("call_graph")
        before = deepcopy(legacy)
        self.assertEqual(check_flow_structure(legacy), [])
        normalized, _ = normalize_flow(legacy)
        self.assertNotIn("sequence", normalized[0])
        self.assertNotIn("call_graph", normalized[0])
        text = "\n".join(native_diagram_sections(legacy))
        self.assertEqual(text.count("尚未提供这类结构化数据"), 2)
        self.assertEqual(legacy, before)
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            old_rule = run / "inputs/methodologies/builtin/scene_flow.md"
            old_rule.parent.mkdir(parents=True)
            old_rule.write_text("Frozen legacy flow without sequence/call_graph", encoding="utf-8")
            old_scene = scene_spec("module-analysis")
            write_json(run / "inputs/analysis-scene.json", old_scene)
            scene_bytes = (run / "inputs/analysis-scene.json").read_bytes()
            rule_bytes = old_rule.read_bytes()
            self.assertEqual(freeze_scene(run, {"analysis_profile": "behavior-test-v2", "analysis_settings": {"scenario": "module-analysis"}}), old_scene)
            self.assertEqual((run / "inputs/analysis-scene.json").read_bytes(), scene_bytes)
            self.assertEqual(old_rule.read_bytes(), rule_bytes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
