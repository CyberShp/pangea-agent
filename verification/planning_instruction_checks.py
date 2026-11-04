"""检查运行入口的规划/分析职责分离；不评价模型语义质量。"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PlanningInstructionChecks(unittest.TestCase):
    def test_source_first_planning_roles_only_allocate(self):
        for directory in (".agents/pangea", ".opencode/agents"):
            with self.subTest(client=directory):
                text = (ROOT / directory / "planning-worker.md").read_text()
                self.assertIn("Planning 只定位入口、分配主责与必要参考", text)
                self.assertIn("不按 inputs 顺序逐项通读", text)
                self.assertIn("planning_handoff", text)
                self.assertIn("relates_to", text)
                self.assertNotIn("v2所有场景先理解模块并保存结构化业务流程", text)
                self.assertNotIn("写完再次 pangea_result_read", text)
                self.assertNotIn("完成后回读规划", text)
                self.assertIn("旧任务仍按其实际冻结规划合同执行", text)

    def test_analysis_reuses_evidence_without_skipping_verification(self):
        for directory in (".agents/pangea", ".opencode/agents"):
            with self.subTest(client=directory):
                text = (ROOT / directory / "analysis-worker.md").read_text()
                self.assertIn("planning_handoff", text)
                self.assertIn("不机械重复 Planning 的搜索与目录巡检", text)
                self.assertIn("仍由本 worker 核对", text)
                self.assertIn("旧任务没有此输入时按原冻结合同执行", text)
                self.assertIn("FIRST-CASE-REFERENCE:BEGIN", text)
                self.assertIn("FLOW-REFERENCE:BEGIN", text)

    def test_desktop_cli_distinguishes_roles(self):
        text = (ROOT / "docs/source-first-cli-worker.md").read_text()
        self.assertIn("source_first_plan 读取冻结规划规则", text)
        self.assertIn("source_first_analysis 完整读取", text)
        self.assertIn("inputs 是可读取目录，不是逐项通读清单", text)
        self.assertIn("planning_handoff", text)
        self.assertIn("next_cursor", text)
        self.assertIn("不派发 Reviewer", text)
        self.assertIn("不查历史 Run 补造交接", text)

    def test_old_claude_graph_does_not_get_source_first_handoff(self):
        for role in ("planning", "analysis"):
            text = (ROOT / ".claude/agents" / f"{role}-worker.md").read_text()
            self.assertNotIn("planning_handoff", text)


if __name__ == "__main__":
    unittest.main()
