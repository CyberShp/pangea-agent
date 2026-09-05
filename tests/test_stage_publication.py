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
