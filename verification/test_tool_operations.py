"""Synthetic frozen-input → stored operations → report checks, NOT model/device acceptance."""
import sys
from pathlib import Path

# Embedded Python does not add the script directory to sys.path.
# Keep sibling verification helpers local to this acceptance process.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from copy import deepcopy
import unittest

import test_tool_sources as fixtures
from pangea_agent.agent_io import read_json
from pangea_agent.cli.adapter_api import bind_action, settle_action
from pangea_agent.cli.source_first_api import plan_write, source_read, source_search, result_write, work_finish
from pangea_agent.graph.result_store import read_result
from pangea_agent.report.source_first import _behavior_markdown, _html, _record_anchor


class ToolOperations(unittest.TestCase):
    setUp = fixtures.TestToolSources.setUp
    put = fixtures.TestToolSources.put
    create = fixtures.TestToolSources.create

    def test_frozen_reference_note_case_and_report(self):
        # This authored fixture declares its own observable contract. Nothing is executed.
        tool = '''# probe fixture v1; run on host in installed tools directory.
# connect performs authentication automatically; lease expires after duration-ms.
# query returns {"sessions": [{"name": "demo", "device": "dev-demo"}]} or {"sessions": []}.
import argparse
p = argparse.ArgumentParser()
s = p.add_subparsers(dest="command")
c = s.add_parser("connect")
c.add_argument("--name", required=True)
c.add_argument("--duration-ms", type=int, required=True)
s.add_parser("query")
'''
        self.put("tools", "scripts/probe.py", tool)
        created, run, planning = self.create([{"repo_id": "tools", "paths": ["scripts"]}])
        run_id = created["run_id"]
        binding = (str(self.root), run_id, planning["action_id"], "planner")
        bind_action(*binding)
        saved = plan_write(*binding, expected_revision=0, unit={"title": "连接", "purpose": "连接及认证中断目标", "owned_files": planning["owned_scope_paths"]})
        work_finish(*binding, revision=saved["revision"])
        settle_action(*binding[:3])
        action = next(a for a in read_json(run / "progress.json")["actions"].values() if a["role"] == "analysis")
        task = read_json(Path(action["task_path"]))
        unit = task["unit_id"]
        binding = (str(self.root), run_id, action["action_id"], "worker")
        bind_action(*binding)
        self.put("tools", "scripts/probe.py", "new source with pause; not this Run")
        frozen = source_read(*binding, repo_id="tools", path="scripts/probe.py")
        self.assertIn('s.add_parser("connect")', str(frozen))
        self.assertNotIn("not this Run", str(frozen))
        search = source_search(*binding, repo_id="tools", path="scripts/probe.py", query="pause")
        self.assertEqual(search["hits"], [])
        note = {"kind": "note", "body": {"note_id": "OP-connect", "title": "连接与查询操作说明", "content": [
            "主机安装的 tools 目录运行；仅适用 probe fixture v1，目标服务已部署。",
            "python scripts/probe.py connect --name <name> --duration-ms <milliseconds>；name 是本次连接名，milliseconds 是租约毫秒数。",
            "connect 自动完成认证，不人工逐消息发送；python scripts/probe.py query 查看 sessions 中的 name 和 device。",
            "租约到期自动结束；再次 query 确认 sessions 中移除本次连接。没有暂停或手动恢复入口。",
            "已核对冻结资料，未实际执行。"], "source_evidence": ["tools:scripts/probe.py:1-10"]}}
        saved = result_write(*binding, expected_revision=0, records=[note])
        records = read_result(Path(task["result_path"]))
        note_id = records.records[0].record_id
        body = {"format_version": "behavior-test-case-v1", "case_id": "C-connect", "title": "创建租约连接并查询", "purpose": "branch", "test_level": "business_blackbox", "execution_readiness": "ready", "readiness_reason": "冻结 fixture 声明 connect/query 及租约自动结束；仅设计依据", "execution_status": "not_run", "preconditions": ["probe fixture v1；主机 tools 目录；目标已部署；query 中不存在 demo"], "steps": [{"action": "python scripts/probe.py connect --name demo --duration-ms 1000", "expected": "自动认证完成；在租约内 query 可看到 name=demo、device=dev-demo"}, {"action": "租约结束后执行 python scripts/probe.py query", "expected": "sessions 中不再包含 demo"}], "external_observations": ["query 的 sessions 名称与设备"], "cleanup": ["租约到期后 query 确认 demo 移除"]}
        unsupported = deepcopy(body)
        unsupported.update(case_id="C-pause", title="认证中断后恢复目标", execution_readiness="needs_instrumentation", readiness_reason="已选工具只有 connect/query；缺少暂停认证窗口、确认命中和解除控制设施", steps=[{"action": "待提供认证窗口控制及确认设施；当前工具不可制造该条件", "expected": "保留认证中断后恢复目标；具体外部结果依据待确认"}], cleanup=["控制设施及其撤销方法待提供"])
        saved = result_write(*binding, expected_revision=saved["revision"], records=[{"kind": "test_case", "body": b, "relates_to": [{"unit_id": unit, "record_id": note_id}]} for b in (body, unsupported)])
        result = read_result(Path(task["result_path"]))
        self.assertEqual(result.records[1].relates_to, [{"unit_id": unit, "record_id": note_id}])
        report_action = {**action, "task": task}
        md = _behavior_markdown({"data_root": str(self.root), "run_id": run_id}, {"run_id": run_id, "lifecycle_status": "running", "quality_status": "UNRESOLVED"}, [(report_action, result)], {})
        anchor = _record_anchor(unit, note_id)
        self.assertIn(f"[{note_id}](#{anchor})", md)
        for text in ("连接与查询操作说明", "--duration-ms 1000", "待补执行条件的用例", "认证中断后恢复目标", "未实际执行"):
            self.assertIn(text, md)
        html = _html(md)
        self.assertIn(f'href="#{anchor}"', html)
        self.assertIn(f'id="{anchor}"', html)
        # Unknown references stay readable and do not fail result-write.
        result_write(*binding, expected_revision=saved["revision"], records=[{"kind": "note", "body": "保留未找到引用", "relates_to": [{"unit_id": "absent", "record_id": "absent"}]}])
        result = read_result(Path(task["result_path"]))
        md = _behavior_markdown({"data_root": str(self.root), "run_id": run_id}, {"run_id": run_id}, [(report_action, result)], {})
        self.assertIn("未找到关联记录，待核实", md)


if __name__ == "__main__":
    unittest.main()
