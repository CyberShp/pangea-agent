from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


def load_guard():
    path = Path(__file__).parents[1] / "src/pangea_agent/skill_packages/codetalks-skill/scripts/run_guard.py"
    spec = importlib.util.spec_from_file_location("codetalks_run_guard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 run_guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StagePublicationTests(unittest.TestCase):
    def test_workbench_projection_documents_complete_risk_details_without_making_them_hard_required(self) -> None:
        root = Path(__file__).parents[1]
        schema = json.loads((root / "schemas/workbench_projection.schema.json").read_text(encoding="utf-8"))
        risk = schema["properties"]["risks"]["items"]
        self.assertEqual(risk["properties"]["severity"]["enum"], ["Critical", "High", "Medium", "Low"])
        for field in (
            "risk_id", "narrative", "trigger", "system_result", "residual_effect",
            "apparent_normality", "external_observation", "blackbox_proof", "severity_source",
            "source_section",
        ):
            self.assertIn(field, risk["properties"])
        self.assertNotIn("required", risk)

        step_05 = (root / "src/pangea_agent/skill_packages/codetalks-skill/steps/05-scenario-expansion.md").read_text(encoding="utf-8")
        step_09 = (root / "src/pangea_agent/skill_packages/codetalks-skill/steps/09-final-delivery.md").read_text(encoding="utf-8")
        self.assertIn('"severity": "High"', step_05)
        self.assertIn('"residual_effect"', step_05)
        self.assertIn("相同 risk_id", step_09)

    def test_completed_run_validation_allows_step_09_formal_outputs(self) -> None:
        guard = load_guard()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run-final"
            for name in ("活文档", "内部索引", "正式输出"):
                (root / name).mkdir(parents=True, exist_ok=True)
            for name in ("运行状态.json", "运行计划.json", "输入材料索引.json"):
                (root / "内部索引" / name).write_text("{}", encoding="utf-8")
            (root / "正式输出" / "完整分析报告.md").write_text("final", encoding="utf-8")
            manifest = {
                "steps": [
                    {"id": "01", "required": []},
                    {"id": "09", "required": []},
                ],
            }

            incomplete_errors = guard.validate_completed_steps(
                root, {"completed_steps": ["01"]}, manifest,
            )
            final_errors = guard.validate_completed_steps(
                root, {"completed_steps": ["01", "09"]}, manifest,
            )

            self.assertTrue(any("必须保持为空" in error for error in incomplete_errors))
            self.assertFalse(any("必须保持为空" in error for error in final_errors))

    def test_step_timing_records_duration_and_artifact_delta(self) -> None:
        guard = load_guard()
        skill_root = Path(__file__).parents[1] / "src/pangea_agent/skill_packages/codetalks-skill"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run-timing"
            guard.command_init(Namespace(
                workspace=str(root), skill_root=str(skill_root), source_raw="raw",
                source_verified="verified", output=None, scenario="module-analysis", mode="depth",
            ))
            state_path = root / "内部索引/运行状态.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["core_rules_ack"] = {key: {"file": "fixture"} for key in guard.load_manifest(state)["required_core_rules"]}
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

            guard.command_start(Namespace(workspace=str(root), step="01"))
            guard.command_progress(Namespace(
                workspace=str(root), step="01", total=1, completed=1,
                unit_label="材料", item_id="scope", item_title="范围", status=None,
            ))
            (root / "活文档" / "01-范围与任务契约.md").write_text("范围与任务契约\n" + "这是用于性能回归的自然语言记录。" * 80, encoding="utf-8")
            (root / "内部索引" / "方法论选择.json").write_text(json.dumps({
                "schema_version": "1.0",
                "selected": [{"methodology_id": "codetalks-skill", "reason": "fixture", "evidence": ["fixture"]}],
                "excluded": [],
            }, ensure_ascii=False), encoding="utf-8")
            guard.command_complete(Namespace(workspace=str(root), step="01"))

            state = json.loads(state_path.read_text(encoding="utf-8"))
            timing = state["performance"]["steps"]["01"]
            self.assertIsInstance(timing["started_at"], str)
            self.assertIsInstance(timing["ended_at"], str)
            self.assertIsInstance(timing["duration_ms"], int)
            self.assertGreaterEqual(timing["duration_ms"], 0)
            self.assertEqual(timing["progress_updates"], 1)
            self.assertGreater(timing["artifact_bytes_delta"], 0)

    def test_stage_projection_is_published_as_draft_with_revision(self) -> None:
        guard = load_guard()
        skill_root = Path(__file__).parents[1] / "src/pangea_agent/skill_packages/codetalks-skill"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run-1"
            guard.command_init(Namespace(
                workspace=str(root), skill_root=str(skill_root), source_raw="raw",
                source_verified="verified", output=None, scenario="module-analysis", mode="depth",
            ))
            state_path = root / "内部索引/运行状态.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["current_step"] = "05"
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            source = root / "candidate.json"
            source.write_text(json.dumps({
                "schema_version": "1.0", "run_id": "run-1",
                "business_flows": [], "risks": [{"risk_id": "R-1"}],
                "test_cases": [], "evidence": [], "review_issues": [],
            }, ensure_ascii=False), encoding="utf-8")

            guard.command_publish_stage(Namespace(
                workspace=str(root), step="05", projection=str(source),
            ))

            published = json.loads((root / "内部索引/工作台投影.json").read_text(encoding="utf-8"))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(published["publication"], state["publication"])
            self.assertEqual(published["publication"]["state"], "draft")
            self.assertEqual(published["publication"]["revision"], 1)
            self.assertEqual(published["publication"]["step_id"], "05")

    def test_speed_mode_does_not_require_independent_judge(self) -> None:
        guard = load_guard()
        skill_root = Path(__file__).parents[1] / "src/pangea_agent/skill_packages/codetalks-skill"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run-speed"
            guard.command_init(Namespace(
                workspace=str(root), skill_root=str(skill_root), source_raw="raw",
                source_verified="verified", output=None, scenario="root-cause", mode="speed",
            ))
            state = json.loads((root / "内部索引/运行状态.json").read_text(encoding="utf-8"))
            self.assertFalse(state["judge"]["required"])
            self.assertEqual(state["publication"]["state"], "pending")

    def test_resume_init_preserves_checkpoint_and_records_resume(self) -> None:
        guard = load_guard()
        skill_root = Path(__file__).parents[1] / "src/pangea_agent/skill_packages/codetalks-skill"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run-resume"
            guard.command_init(Namespace(
                workspace=str(root), skill_root=str(skill_root), source_raw="raw",
                source_verified="verified", output=None, scenario="module-analysis", mode="depth",
            ))
            state_path = root / "内部索引/运行状态.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["status"] = "in_progress"
            state["current_step"] = "04"
            state["completed_steps"] = ["01", "02", "03"]
            state["resume_count"] = 2
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

            guard.command_init(Namespace(
                workspace=str(root), skill_root=str(skill_root), source_raw="raw",
                source_verified="verified", output=None, scenario="module-analysis", mode="depth",
                resume=True,
            ))

            resumed = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(resumed["current_step"], "04")
            self.assertEqual(resumed["completed_steps"], ["01", "02", "03"])
            self.assertEqual(resumed["resume_count"], 3)
            self.assertEqual(resumed["status"], "in_progress")
            self.assertIsInstance(resumed["last_resumed_at"], str)


if __name__ == "__main__":
    unittest.main()
