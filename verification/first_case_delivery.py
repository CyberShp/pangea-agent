"""首写回放/冻结输入传递；使用人工语料，不宣称真实模型已遵从。"""
from pathlib import Path
import json
import tempfile
import unittest

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.cli.adapter_api import bind_action, settle_action
from pangea_agent.cli.run_module_analysis import run_module_analysis
from pangea_agent.cli.source_first_api import input_read, plan_write, result_write, task_open, work_finish
from pangea_agent.graph.result_store import read_result
from pangea_agent.inventory.source_access import compact_task_view

ROOT = Path(__file__).resolve().parents[1]


class FirstCaseDelivery(unittest.TestCase):
    def test_compact_keeps_execution_context_before_unrelated_metadata(self):
        context = {"analysis_settings": {"objective": "interface-contract", "mode": "speed"},
                   "target": "公开 API 契约", "focus": ["输入及资源"], "title": "接口",
                   "purpose": "本单元负责公开入口", "test_tool_reference_paths": [{"repo_id": "r", "path": "tool.py"}]}
        task = {**{f"metadata-{i}": "x" * 1800 for i in range(20)}, **context,
                "owned_regions": [{"region_id": str(i)} for i in range(2000)],
                "inputs": [{"input_id": str(i)} for i in range(2000)]}
        view = compact_task_view(task)
        for key, value in context.items():
            self.assertEqual(view[key], value)
        for key in ("owned_regions", "inputs"):
            self.assertEqual(view["deferred_fields"][key]["input_id"], f"task:{key}")
        self.assertIn("next_cursor", view["execution_context_navigation"])
        self.assertNotIn("analysis_settings", compact_task_view({}))

    def test_first_case_is_persisted_before_any_reviewer_without_semantic_rewriting(self):
        corpus = json.loads((ROOT / "verification/fixtures/blackbox-semantic-examples-v1.json").read_text())
        selected = {"internal-oracle", "public-api-boundary", "missing-instrumentation", "unknown-skeleton", "exact-protocol-trigger"}
        for pair in corpus["pairs"]:
            if pair["id"] not in selected:
                continue
            # Negatives must remain writable: semantics belong to the worker,
            # not to Python. Positives are authored fixtures, not model output.
            for side in ("positive", "negative"):
                with self.subTest(pair=pair["id"], side=side), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    repo = root / "repositories/demo/src"
                    repo.mkdir(parents=True)
                    (repo / "entry.c").write_text("int public_entry(void) { return 0; }\n")
                    request = root / "request.json"
                    write_json(request, {"data_root": directory, "repository": "demo", "target": "入口行为",
                                         "source_scope": ["src"], "analysis_profile": "behavior-test-v2",
                                         "analysis_settings": {"scenario": "branch-analysis", "mode": "speed"}})
                    run_id = run_module_analysis(str(request))["run_id"]
                    run = root / "runs" / run_id
                    def actions():
                        return list(read_json(run / "progress.json")["actions"].values())
                    plan = next(a for a in actions() if a["role"] == "planning")
                    bind_action(directory, run_id, plan["action_id"], "planner")
                    saved = plan_write(directory, run_id, plan["action_id"], "planner", expected_revision=0,
                                       unit={"title": "入口", "purpose": "入口与终态", "owned_files": [{"repo_id": "demo", "path": "src/entry.c"}]})
                    work_finish(directory, run_id, plan["action_id"], "planner", revision=saved["revision"])
                    settle_action(directory, run_id, plan["action_id"])
                    action = next(a for a in actions() if a["role"] == "analysis")
                    bind_action(directory, run_id, action["action_id"], "original-worker")
                    args = (directory, run_id, action["action_id"], "original-worker")
                    opened = task_open(*args)["task"]
                    self.assertEqual(opened["purpose"], "入口与终态")
                    parts, cursor = [], None
                    while True:
                        page = input_read(*args, input_id="rubric_scene_common_generation", cursor=cursor, max_chars=800)
                        parts.append(page["text"])
                        cursor = page["next_cursor"]
                        if cursor is None:
                            break
                    self.assertIn("FIRST-CASE-DELIVERY", "".join(parts))
                    body = pair[side]["body"]
                    saved = result_write(*args, expected_revision=0, records=[{"kind": "test_case", "body": body}])
                    first = read_result(opened["result_path"])
                    self.assertEqual(saved["revision"], 1)
                    self.assertEqual(first.records[0].body, body)
                    self.assertFalse(any(a["role"] == "review" for a in actions()))
                    # Resume the same action and stream a second record; no full-unit barrier.
                    self.assertEqual(task_open(*args)["task"]["result_path"], opened["result_path"])
                    result_write(*args, expected_revision=1, records=[{"kind": "note", "body": "继续本单元，未审结"}])
                    self.assertEqual(read_result(opened["result_path"]).records[0].body, body)
                    self.assertFalse(any(a["role"] == "review" for a in actions()))

    def test_all_runtime_entrypoints_reference_frozen_first_write_rules(self):
        for name in (".agents/pangea/analysis-worker.md", ".claude/agents/analysis-worker.md",
                     ".opencode/agents/analysis-worker.md", ".opencode/skills/product-blackbox-test-case/SKILL.md",
                     "docs/source-first-cli-worker.md"):
            text = (ROOT / name).read_text()
            self.assertIn("若当前 task 的冻结生成 rubric 含 FIRST-CASE-DELIVERY", text)
            self.assertIn("未提供该段的旧 Run 继续原合同", text)


if __name__ == "__main__":
    print("仅验证首写回放、冻结规则装载和输入传递；真实模型首轮质量未验证。", flush=True)
    unittest.main(verbosity=2)
