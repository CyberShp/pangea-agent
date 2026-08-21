from __future__ import annotations

import atexit
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from docx import Document
from PIL import Image
from openpyxl import Workbook

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.cli.run_module_analysis import run_module_analysis
from pangea_agent.documents.coverage import match_coverage_records, parse_coverage_xlsx
from pangea_agent.graph.nodes.advance_run import advance_run
from pangea_agent.graph.nodes.load_contract import load_contract
from pangea_agent.graph.nodes.resolve_repositories import resolve_repositories
from pangea_agent.graph.nodes.locate_module import locate_module
from pangea_agent.graph.nodes.index_materials import index_materials
from pangea_agent.graph.nodes.prepare_worker_tasks import (
    _coverage_context,
    _related_state_context,
    _source_inventory,
)
from pangea_agent.graph.run_store import load_worker_task
from pangea_agent.models.worker import AnalysisUnit
from pangea_agent.inventory.source_scanner import _known_macro_parse_artifact
from pangea_agent.inventory.lua_symbols import parse_lua_file
from pangea_agent.report.html import render_html_report
from pangea_agent.report.markdown import render_report


Scenario = Callable[[], None]
_TEMP_ROOTS: list[Path] = []


@atexit.register
def _cleanup_temp_roots() -> None:
    for root in _TEMP_ROOTS:
        shutil.rmtree(root, ignore_errors=True)


def _workspace(*, run_id: str = "smoke-01", repositories: tuple[str, ...] = ("demo",)) -> tuple[Path, Path, Path]:
    root = Path(tempfile.mkdtemp(prefix="pangea-v1-smoke-"))
    _TEMP_ROOTS.append(root)
    data_root = root / "data"
    for repo_id in repositories:
        repo = data_root / "repositories" / repo_id
        module = repo / "module"
        module.mkdir(parents=True)
        (module / "entry.c").write_text(f"int {repo_id}_start(void) {{ return 0; }}\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "module/entry.c"], check=True)
        subprocess.run(
            [
                "git", "-C", str(repo), "-c", "user.name=PANGEA Smoke",
                "-c", "user.email=pangea-smoke@example.invalid", "commit", "-qm", "fixture",
            ],
            check=True,
        )
    contract = {
        "run_id": run_id,
        "data_root": str(data_root),
        "mode": "module_analysis",
        "repositories": list(repositories),
        "target": "CHAP",
        "source_scope": ["module"],
    }
    contract_path = root / "contract.json"
    write_json(contract_path, contract)
    return root, data_root, contract_path


def _lua_workspace() -> tuple[Path, Path, Path]:
    root, data_root, contract_path = _workspace(repositories=("lua-demo",))
    repo = data_root / "repositories" / "lua-demo"
    module = repo / "module"
    (module / "entry.c").unlink()
    (module / "helper.lua").write_text(
        "local M = {}\nfunction M.value() return 1 end\nreturn M\n",
        encoding="utf-8",
    )
    (module / "component.lua").write_text(
        """local mc = require("mc")
local helper = require("module.helper")
local Component = mc.class("Component")
Component.changed = mc.signal()

function Component:ctor()
    self.ready = false
end

function Component:pre_init()
    self.value = helper.value()
end

function Component:init()
    self.changed:connect(function() self.ready = true end)
end

function Component:run()
    if not self.ready then error("not ready") end
    return pcall(function() self.changed:emit() end)
end

return Component
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        [
            "git", "-C", str(repo), "-c", "user.name=PANGEA Smoke",
            "-c", "user.email=pangea-smoke@example.invalid", "commit", "-qm", "lua fixture",
        ],
        check=True,
    )
    contract = read_json(contract_path)
    contract["source_scope"] = ["module/component.lua"]
    write_json(contract_path, contract)
    return root, data_root, contract_path


def _task_result(task: dict, *, finish_reason: str = "stop", fake_location: bool = False) -> dict:
    repo_id = task["unit"]["repo_id"]
    source_path = task["unit"]["source_scope"][0]
    chunk_id = f"{repo_id}:{source_path}:1-1"
    location = "fake:missing.c:1-1" if fake_location else chunk_id
    evidence = {"chunk_id": chunk_id, "location": location, "observation": "模块入口实现"}
    return {
        "schema_version": "1.0",
        "run_id": task["run_id"],
        "unit_id": task["unit"]["unit_id"],
        "worker_id": f"worker-{task['unit']['unit_id']}",
        "attempt": task["attempt"],
        "finish_reason": finish_reason,
        "summary": "完成当前单元分析",
        "analyzed_scope": task["unit"]["source_scope"],
        "analyzed_context_scope": task["unit"].get("context_scope", []),
        "evidence": [evidence],
        "business_flows": [{
            "title": "模块启动",
            "description": "测试人员启动目标模块。",
            "steps": ["启动目标模块"],
            "mermaid": None,
            "evidence": [evidence],
        }],
        "visual_findings": [],
        "risks": [],
        "test_cases": [],
        "addressed_review_issue_ids": [item["issue_id"] for item in task["review_issues"]],
        "errors": [] if finish_reason == "stop" else [finish_reason],
        "analysis_checkpoint": {
            "source_paths_reviewed": task["unit"]["source_scope"],
            "lifecycle_stages_checked": ["初始化", "运行", "停止", "恢复"],
            "failure_paths": [{
                "path_id": item["check_id"],
                "linked_risk_ids": [],
                "trigger": "入口调用",
                "side_effects": "进入模块逻辑",
                "failure": "无已确认故障",
                "caller_handling": "调用方读取返回值",
                "final_states": "模块保持可用",
                "disposition": "excluded",
            } for item in task.get("semantic_check_items", [])] or [{
                "path_id": "F-001",
                "linked_risk_ids": [],
                "trigger": "入口调用",
                "side_effects": "进入模块逻辑",
                "failure": "无已确认故障",
                "caller_handling": "调用方读取返回值",
                "final_states": "模块保持可用",
                "disposition": "excluded",
            }],
            "material_decisions": [],
            "coverage_priorities": [],
            "coverage_decisions": [],
            "risk_set_frozen": True,
            "counterexamples_checked": ["异常返回不会被误写为成功"],
        },
    }


def _test_case(
    case_id: str,
    *,
    risk_ids: list[str] | None = None,
    requirement_ids: list[str] | None = None,
    material_ids: list[str] | None = None,
    coverage_ids: list[str] | None = None,
    title: str = "业务行为验证",
) -> dict:
    return {
        "test_case_id": case_id,
        "title": title,
        "case_type": "功能",
        "linked_risk_ids": risk_ids or [],
        "linked_requirement_ids": requirement_ids or [],
        "linked_material_ids": material_ids or [],
        "linked_coverage_ids": coverage_ids or [],
        "preconditions": ["环境就绪"],
        "steps": ["执行目标业务操作"],
        "expected_results": ["系统表现符合测试依据"],
        "observability": ["业务结果"],
        "cleanup": ["恢复环境"],
    }


def _mismatched_step_results_rejected() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    task = read_json(Path(state["agent_task_paths"][0]))
    result = _task_result(task)
    result["risks"] = [{
        "risk_id": "U00-R1",
        "title": "可执行风险",
        "affected_paths": task["unit"]["source_scope"],
        "dfx": ["功能与状态"],
        "severity": "Medium",
        "confidence": "high",
        "trigger": "业务触发",
        "system_result": "系统异常",
        "external_observation": "外部可观测",
        "exclusion_condition": "排除条件",
        "upstream_semantics": {
            "reachability": "业务入口可达",
            "caller_constraints": "调用方未消除",
            "documented_behavior": "资料未定义为预期",
            "existing_tests": "已有测试未覆盖",
            "conclusion": "risk_remains",
        },
        "translation_status": "Blackbox-ready",
        "status": "pending",
        "evidence": result["evidence"],
    }]
    result["test_cases"] = [{
        "test_case_id": "U00-TC1",
        "title": "步骤与预期错位",
        "case_type": "功能",
        "linked_risk_ids": ["U00-R1"],
        "linked_requirement_ids": [],
        "linked_material_ids": [],
        "linked_coverage_ids": [],
        "preconditions": ["环境就绪"],
        "steps": ["准备环境", "触发业务"],
        "expected_results": ["系统异常"],
        "observability": ["外部日志"],
        "cleanup": ["恢复环境"],
    }]
    write_json(Path(task["result_path"]), result)
    assert run_module_analysis(str(contract))["phase"] == "WAITING_ANALYSIS"
    errors = read_json(data_root / "runs" / "smoke-01" / "progress.json")["errors"]
    assert any("每个测试步骤必须有且只有一个对应的预期结果" in item["reason"] for item in errors)


def _requirement_only_test_case_is_accepted() -> None:
    _, _, contract = _workspace()
    state = run_module_analysis(str(contract))
    task = read_json(Path(state["agent_task_paths"][0]))
    result = _task_result(task)
    result["test_cases"] = [_test_case(
        "U00-REQ-TC1",
        requirement_ids=["REQ-DEMO-01"],
        title="需求行为补测",
    )]
    write_json(Path(task["result_path"]), result)
    assert run_module_analysis(str(contract))["phase"] == "WAITING_REVIEW"


def _semantic_check_risk_scope_is_enforced() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    task_path = Path(state["agent_task_paths"][0])
    task = read_json(task_path)
    task["semantic_check_items"] = [{
        "check_id": "SC-DEMO-PAIR-01",
        "kind": "paired_operation",
        "subject_path": "module/entry.c",
        "instruction": "只检查当前实现",
        "context_paths": ["module/entry.c"],
    }]
    write_json(task_path, task)
    result = _task_result(task)
    result["risks"] = [{
        "risk_id": "U00-R-SCOPE",
        "title": "错误实现范围",
        "affected_paths": ["module/other.c"],
        "dfx": ["功能与状态"],
        "severity": "Medium",
        "confidence": "high",
        "trigger": "业务触发",
        "system_result": "系统异常",
        "external_observation": "外部可观测",
        "exclusion_condition": "排除条件",
        "upstream_semantics": {
            "reachability": "业务入口可达",
            "caller_constraints": "调用方未消除",
            "documented_behavior": "资料未定义为预期",
            "existing_tests": "已有测试未覆盖",
            "conclusion": "risk_remains",
        },
        "translation_status": "Developer-confirm",
        "status": "pending",
        "evidence": result["evidence"],
    }]
    result["analysis_checkpoint"]["failure_paths"] = [{
        "path_id": "SC-DEMO-PAIR-01",
        "linked_risk_ids": ["U00-R-SCOPE"],
        "trigger": "入口调用",
        "side_effects": "进入模块逻辑",
        "failure": "配对失败",
        "caller_handling": "调用方继续",
        "final_states": "系统异常",
        "disposition": "risk",
    }]
    write_json(Path(task["result_path"]), result)
    assert run_module_analysis(str(contract))["phase"] == "WAITING_ANALYSIS"
    errors = read_json(data_root / "runs" / "smoke-01" / "progress.json")["errors"]
    assert any("未声明 semantic check" in item["reason"] for item in errors)


def _write_all_analysis(state: dict) -> None:
    for task_path in state["agent_task_paths"]:
        task = read_json(Path(task_path))
        write_json(Path(task["result_path"]), _task_result(task))


def _advance_to_review_comparison(
    data_root: Path,
    run_id: str,
    *,
    reviewer: str = "reviewer-1",
    extra_findings: list[dict] | None = None,
) -> tuple[dict, list[dict]]:
    run_dir = data_root / "runs" / run_id
    task = read_json(run_dir / "agent-tasks" / "review-independent.json")
    assert task["stage"] == "independent_review"
    assert task["analysis_results"] == []
    findings = []
    for reference in task["analysis_tasks"]:
        worker_task = read_json(Path(reference["task_path"]))
        findings.extend({
            "unit_id": reference["unit_id"],
            "check_id": item["check_id"],
            "finding": f"独立核对 {item['check_id']} 未发现额外问题",
            "evidence": [f"{worker_task['unit']['repo_id']}:{item['subject_path']}:1"],
        } for item in worker_task["semantic_check_items"])
    findings.extend(extra_findings or [])
    write_json(Path(task["result_path"]), {
        "schema_version": "1.0",
        "run_id": run_id,
        "reviewer_id": reviewer,
        "finish_reason": "stop",
        "summary": "独立复核完成",
        "reviewed_units": [item["unit_id"] for item in task["analysis_tasks"]],
        "findings": findings,
    })
    state = run_module_analysis(str(run_dir / "inputs" / "task-contract.json"))
    assert state["phase"] == "WAITING_REVIEW_COMPARISON"
    comparison = read_json(run_dir / "agent-tasks" / "review.json")
    assert comparison["stage"] == "comparison_review"
    assert comparison["same_reviewer_id"] == reviewer
    return comparison, findings


def _review(data_root: Path, run_id: str, *, status: str, reviewer: str = "reviewer-1") -> None:
    task, findings = _advance_to_review_comparison(data_root, run_id, reviewer=reviewer)
    issues = [] if status == "PASS" else [{
        "issue_id": "I-001",
        "unit_id": task["analysis_results"][0]["unit_id"],
        "reason": "缺少异常分支说明",
        "required_change": "补充异常分支分析",
    }]
    write_json(Path(task["result_path"]), {
        "schema_version": "1.0",
        "run_id": run_id,
        "reviewer_id": reviewer,
        "finish_reason": "stop",
        "status": status,
        "summary": "复核完成",
        "issues": issues,
        "reviewed_units": [item["unit_id"] for item in task["analysis_results"]],
        "independent_findings": [
            {**finding, "worker_disposition": "covered"}
            for finding in findings
        ],
    })


def _pass_report() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    _write_all_analysis(state)
    state = run_module_analysis(str(contract))
    assert state["phase"] == "WAITING_REVIEW"
    _review(data_root, "smoke-01", status="PASS")
    state = run_module_analysis(str(contract))
    assert state["phase"] == "COMPLETE"
    assert Path(state["report_path"]).is_file() and Path(state["html_report_path"]).is_file()
    markdown = Path(state["report_path"]).read_text(encoding="utf-8")
    html_report = Path(state["html_report_path"]).read_text(encoding="utf-8")
    assert markdown.startswith("# CHAP 分析报告")
    assert "<title>CHAP 分析报告</title>" in html_report
    assert "PANGEA Agent 测试分析报告" not in markdown + html_report
    assert "COMPLETE / PASS" not in markdown + html_report
    assert "| 项目 | 内容 |" in markdown
    assert "| 类别 | 源码仓 | 路径 | 纳入原因 |" in markdown
    assert "## 2. 分析引用范围" in markdown
    assert "质量门禁已通过。完成" in markdown and "质量门禁已通过。完成" in html_report


def _review_missing_finding_cannot_pass() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    _write_all_analysis(state)
    state = run_module_analysis(str(contract))
    assert state["phase"] == "WAITING_REVIEW"
    extra = {
        "unit_id": "U00",
        "check_id": "LIFECYCLE-U00",
        "finding": "独立复核发现一条可达失败路径",
        "evidence": ["demo:module/entry.c:1"],
    }
    task, findings = _advance_to_review_comparison(
        data_root,
        "smoke-01",
        extra_findings=[extra],
    )
    write_json(Path(task["result_path"]), {
        "schema_version": "1.0",
        "run_id": "smoke-01",
        "reviewer_id": "reviewer-1",
        "finish_reason": "stop",
        "status": "PASS",
        "summary": "发现遗漏但错误放行",
        "issues": [],
        "reviewed_units": [item["unit_id"] for item in task["analysis_results"]],
        "independent_findings": [
            {
                **finding,
                "worker_disposition": (
                    "missing" if finding["check_id"] == "LIFECYCLE-U00" else "covered"
                ),
            }
            for finding in findings
        ],
    })
    state = run_module_analysis(str(contract))
    assert state["phase"] == "WAITING_REVIEW_COMPARISON"
    progress = read_json(data_root / "runs" / "smoke-01" / "progress.json")
    assert any("PASS 不能包含 missing" in item["reason"] for item in progress["errors"])


def _comparison_cannot_drop_independent_findings() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    _write_all_analysis(state)
    assert run_module_analysis(str(contract))["phase"] == "WAITING_REVIEW"
    extra = {
        "unit_id": "U00",
        "check_id": "LIFECYCLE-U00",
        "finding": "独立复核记录正常生命周期",
        "evidence": ["demo:module/entry.c:1"],
    }
    task, _ = _advance_to_review_comparison(
        data_root,
        "smoke-01",
        extra_findings=[extra],
    )
    write_json(Path(task["result_path"]), {
        "schema_version": "1.0",
        "run_id": "smoke-01",
        "reviewer_id": "reviewer-1",
        "finish_reason": "stop",
        "status": "PASS",
        "summary": "错误丢弃独立 finding",
        "issues": [],
        "reviewed_units": ["U00"],
        "independent_findings": [],
    })
    state = run_module_analysis(str(contract))
    assert state["phase"] == "WAITING_REVIEW_COMPARISON"
    progress = read_json(data_root / "runs" / "smoke-01" / "progress.json")
    assert any("必须逐项保留独立复核 findings" in item["reason"] for item in progress["errors"])


def _rework_same_reviewer() -> None:
    _, data_root, contract = _lua_workspace()
    state = run_module_analysis(str(contract))
    subprocess.run(
        [
            sys.executable, "-m", "pangea_agent.cli.main", "record-agent-session",
            "--run-id", "smoke-01", "--data-root", str(data_root),
            "--role", "analysis", "--unit-id", "U00", "--task-id", "analysis-worker-1",
        ],
        check=True,
        capture_output=True,
    )
    _write_all_analysis(state)
    run_module_analysis(str(contract))
    _review(data_root, "smoke-01", status="REWORK", reviewer="reviewer-1")
    state = run_module_analysis(str(contract))
    assert state["phase"] == "WAITING_REWORK"
    rework_task = read_json(data_root / "runs" / "smoke-01" / "agent-tasks" / "rework" / "U00.json")
    analysis_task = read_json(data_root / "runs" / "smoke-01" / "agent-tasks" / "analysis" / "U00.json")
    assert rework_task["checkpoint_rubric_paths"] == analysis_task["checkpoint_rubric_paths"]
    assert "src/pangea_agent/rubrics/builtin/lua_analysis.md" in rework_task["checkpoint_rubric_paths"]
    assert "src/pangea_agent/rubrics/builtin/openubmc_analysis.md" in rework_task["checkpoint_rubric_paths"]
    subprocess.run(
        [
            sys.executable, "-m", "pangea_agent.cli.main", "record-agent-session",
            "--run-id", "smoke-01", "--data-root", str(data_root),
            "--role", "rework", "--unit-id", "U00", "--task-id", "replacement-worker-1",
        ],
        check=True,
        capture_output=True,
    )
    rejected = subprocess.run(
        [
            sys.executable, "-m", "pangea_agent.cli.main", "record-agent-session",
            "--run-id", "smoke-01", "--data-root", str(data_root),
            "--role", "rework", "--unit-id", "U00", "--task-id", "replacement-worker-2",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0 and "不能替换 task_id" in rejected.stderr
    write_json(Path(rework_task["result_path"]), _task_result(rework_task))
    state = run_module_analysis(str(contract))
    assert state["phase"] == "WAITING_REWORK_REVIEW"
    review_task = read_json(data_root / "runs" / "smoke-01" / "agent-tasks" / "rework-review.json")
    assert review_task["same_reviewer_id"] == "reviewer-1"
    write_json(Path(review_task["result_path"]), {
        "schema_version": "1.0",
        "run_id": "smoke-01",
        "reviewer_id": "reviewer-1",
        "finish_reason": "stop",
        "status": "PASS",
        "summary": "返工验证通过",
        "issues": [],
        "reviewed_units": [item["unit_id"] for item in review_task["analysis_results"]],
        "independent_findings": [],
    })
    assert run_module_analysis(str(contract))["phase"] == "COMPLETE"


def _truncated_correction() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    task = read_json(Path(state["agent_task_paths"][0]))
    write_json(Path(task["result_path"]), _task_result(task, finish_reason="truncated"))
    assert run_module_analysis(str(contract))["phase"] == "WAITING_ANALYSIS"
    progress_path = data_root / "runs" / "smoke-01" / "progress.json"
    assert read_json(progress_path)["errors"]
    write_json(Path(task["result_path"]), _task_result(task))
    assert run_module_analysis(str(contract))["phase"] == "WAITING_REVIEW"
    progress = read_json(progress_path)
    assert not progress["errors"] and progress["error_history"]


def _reviewer_unavailable_has_explicit_unresolved_path() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    _write_all_analysis(state)
    assert run_module_analysis(str(contract))["phase"] == "WAITING_REVIEW"
    _review(data_root, "smoke-01", status="REWORK", reviewer="reviewer-1")
    state = run_module_analysis(str(contract))
    assert state["phase"] == "WAITING_REWORK"
    rework_task = read_json(
        data_root / "runs" / "smoke-01" / "agent-tasks" / "rework" / "U00.json"
    )
    write_json(Path(rework_task["result_path"]), _task_result(rework_task))
    state = run_module_analysis(str(contract))
    assert state["phase"] == "WAITING_REWORK_REVIEW"

    rejected = subprocess.run(
        [
            sys.executable, "-m", "pangea_agent.cli.main", "mark-reviewer-unavailable",
            "--run-id", "smoke-01", "--data-root", str(data_root),
            "--reviewer-id", "another-reviewer", "--reason", "session missing",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0 and "不是当前 Run 绑定的原 reviewer" in rejected.stderr
    subprocess.run(
        [
            sys.executable, "-m", "pangea_agent.cli.main", "mark-reviewer-unavailable",
            "--run-id", "smoke-01", "--data-root", str(data_root),
            "--reviewer-id", "reviewer-1", "--reason", "saved DSH session cannot be resumed",
        ],
        check=True,
        capture_output=True,
    )
    state = run_module_analysis(str(contract))
    if state["phase"] == "READY_TO_FINALIZE":
        state = run_module_analysis(str(contract))
    assert state["quality_report"]["status"] == "UNRESOLVED"
    assert state["phase"] == "INCOMPLETE"


def _unmatched_evidence_is_pending() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    task = read_json(Path(state["agent_task_paths"][0]))
    forged = _task_result(task, fake_location=True)
    forged["evidence"][0]["chunk_id"] = "missing-chunk"
    forged["business_flows"][0]["evidence"] = forged["evidence"]
    write_json(Path(task["result_path"]), forged)
    assert run_module_analysis(str(contract))["phase"] == "WAITING_REVIEW"
    normalized = read_json(Path(task["result_path"]))
    assert normalized["evidence"][0]["status"] == "pending_confirmation"
    assert normalized["business_flows"][0]["evidence"][0]["status"] == "pending_confirmation"
    assert not read_json(data_root / "runs" / "smoke-01" / "progress.json")["errors"]


def _duplicate_ids_correction() -> None:
    _, data_root, contract = _workspace(repositories=("repo-a", "repo-b"))
    state = run_module_analysis(str(contract))
    tasks = [read_json(Path(path)) for path in state["agent_task_paths"]]
    for task in tasks:
        result = _task_result(task)
        evidence = result["evidence"]
        result["risks"] = [{
            "risk_id": "DUP-R01",
            "title": "重复风险编号",
            "affected_paths": task["unit"]["source_scope"],
            "dfx": ["功能与状态"],
            "severity": "Medium",
            "confidence": "high",
            "trigger": "触发条件",
            "system_result": "系统结果",
            "external_observation": "外部观测",
            "exclusion_condition": "排除条件",
            "upstream_semantics": {
                "reachability": "业务入口可达",
                "caller_constraints": "调用方没有消除该结果",
                "documented_behavior": "资料未定义为预期行为",
                "existing_tests": "已有测试未覆盖该条件",
                "conclusion": "risk_remains",
            },
            "translation_status": "Blackbox-ready",
            "status": "pending",
            "evidence": evidence,
        }]
        result["test_cases"] = [_test_case("DUP-TC01", risk_ids=["DUP-R01"])]
        write_json(Path(task["result_path"]), result)
    assert run_module_analysis(str(contract))["phase"] == "WAITING_REVIEW"
    normalized = [read_json(Path(task["result_path"])) for task in tasks]
    assert {item["risks"][0]["risk_id"] for item in normalized} == {"DUP-R01", "DUP-R01-2"}
    assert {item["test_cases"][0]["test_case_id"] for item in normalized} == {"DUP-TC01", "DUP-TC01-2"}
    assert not read_json(data_root / "runs" / "smoke-01" / "progress.json")["errors"]


def _mechanical_task_change_does_not_block() -> None:
    _, _, contract = _workspace()
    state = run_module_analysis(str(contract))
    task_path = Path(state["agent_task_paths"][0])
    task = read_json(task_path)
    task["result_path"] = "Z:\\wrong\\result.json"
    write_json(task_path, task)
    corrected_path = Path(task_path).parents[2] / "agent-results" / "analysis" / "U00.json"
    write_json(corrected_path, _task_result(task))
    assert run_module_analysis(str(contract))["phase"] == "WAITING_REVIEW"
    assert Path(read_json(task_path)["result_path"]).resolve() == corrected_path.resolve()


def _mechanical_review_fields_do_not_block() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    _write_all_analysis(state)
    assert run_module_analysis(str(contract))["phase"] == "WAITING_REVIEW"
    run_dir = data_root / "runs" / "smoke-01"
    _, findings = _advance_to_review_comparison(data_root, "smoke-01")
    task_path = run_dir / "agent-tasks" / "review.json"
    task = read_json(task_path)
    task["result_path"] = "Z:\\wrong\\review.json"
    write_json(task_path, task)
    write_json(run_dir / "agent-results" / "review.json", {
        "schema_version": "1.0",
        "run_id": "wrong-run",
        "reviewer_id": "reviewer-1",
        "finish_reason": "stop",
        "status": "PASS",
        "summary": "语义复核通过",
        "issues": [],
        "reviewed_units": [item["unit_id"] for item in task["analysis_results"]],
        "independent_findings": [
            {**finding, "worker_disposition": "covered"}
            for finding in findings
        ],
    })
    state = run_module_analysis(str(contract))
    assert state["phase"] == "COMPLETE"
    normalized = read_json(run_dir / "agent-results" / "review.json")
    assert normalized["run_id"] == "smoke-01"


def _missing_scope() -> None:
    _, data_root, contract = _workspace()
    payload = read_json(contract)
    payload["source_scope"] = ["missing"]
    write_json(contract, payload)
    try:
        run_module_analysis(str(contract))
    except ValueError as exc:
        assert "没有可分析源码" in str(exc)
    else:
        raise AssertionError("不存在的源码范围仍派发了任务")
    assert not list((data_root / "runs" / "smoke-01" / "agent-tasks").glob("**/*.json"))


def _report_recovery() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    _write_all_analysis(state)
    run_module_analysis(str(contract))
    _review(data_root, "smoke-01", status="PASS")
    raw_contract = read_json(contract)
    state = load_contract({"run_id": "smoke-01", "data_root": str(data_root), "task_contract": raw_contract})
    assert advance_run(state)["phase"] == "READY_TO_FINALIZE"
    state = run_module_analysis(str(contract))
    Path(state["report_path"]).unlink()
    Path(state["html_report_path"]).unlink()
    recovered = run_module_analysis(str(contract))
    assert recovered["phase"] == "COMPLETE"
    assert Path(recovered["report_path"]).is_file() and Path(recovered["html_report_path"]).is_file()
    run_dir = data_root / "runs" / "smoke-01"
    (run_dir / "final-state.json").unlink()
    Path(recovered["report_path"]).unlink()
    Path(recovered["html_report_path"]).unlink()
    recovered = run_module_analysis(str(contract))
    assert recovered["phase"] == "COMPLETE"
    assert (run_dir / "final-state.json").is_file()
    assert Path(recovered["report_path"]).is_file() and Path(recovered["html_report_path"]).is_file()


def _document_gap() -> None:
    root, data_root, contract = _workspace()
    inbox = data_root / "inbox"
    inbox.mkdir()
    (inbox / "broken.pdf").write_bytes(b"not a pdf")
    image_path = root / "diagram.png"
    Image.new("RGB", (2, 2), "white").save(image_path)
    document = Document()
    document.add_paragraph("CHAP 设计图")
    document.add_picture(str(image_path))
    document.save(inbox / "design.docx")
    state = run_module_analysis(str(contract))
    _write_all_analysis(state)
    run_module_analysis(str(contract))
    _review(data_root, "smoke-01", status="PASS")
    state = run_module_analysis(str(contract))
    assert state["phase"] == "INCOMPLETE"
    assert state["quality_report"]["status"] == "UNRESOLVED"
    kinds = {item.get("kind") for item in state["quality_report"]["unresolved"]}
    assert {"document_parse_warning", "unread_image"} <= kinds


def _coverage_reference_only() -> None:
    _, data_root, contract = _workspace()
    coverage = data_root / "coverage"
    coverage.mkdir()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["模块", "函数", "覆盖次数"])
    sheet.append(["module", "demo_start", 3])
    sheet.append(["module", "not_in_scope", 1])
    workbook.save(coverage / "coverage.xlsx")
    state = run_module_analysis(str(contract))
    report = state["coverage_report"]
    assert len(report["matched"]) == 1 and len(report["unmatched"]) == 1
    assert report["matched"][0]["meaning"] == "function_execution_reference_only"
    task = read_json(Path(state["agent_task_paths"][0]))
    assert task["coverage_context"][0]["gaps"] == []


def _coverage_gap_requires_test_case() -> None:
    _, data_root, contract = _workspace()
    coverage = data_root / "coverage"
    coverage.mkdir()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["模块", "函数", "覆盖次数"])
    sheet.append(["module", "demo_start", 0])
    workbook.save(coverage / "coverage.xlsx")
    state = run_module_analysis(str(contract))
    task = read_json(Path(state["agent_task_paths"][0]))
    gap_id = task["coverage_context"][0]["gaps"][0]["coverage_id"]
    result = _task_result(task)
    write_json(Path(task["result_path"]), result)
    assert run_module_analysis(str(contract))["phase"] == "WAITING_ANALYSIS"
    errors = read_json(data_root / "runs" / "smoke-01" / "progress.json")["errors"]
    assert any("Coverage 缺口尚未闭环" in item["reason"] for item in errors)
    result["test_cases"] = [_test_case("U00-COV-TC1", coverage_ids=[gap_id], title="Coverage 缺口补测")]
    result["analysis_checkpoint"]["coverage_decisions"] = [{
        "coverage_id": gap_id,
        "disposition": "new_coverage_case",
        "linked_test_case_ids": ["U00-COV-TC1"],
        "reason": "通过当前模块业务入口补齐未执行函数路径。",
    }]
    write_json(Path(task["result_path"]), result)
    assert run_module_analysis(str(contract))["phase"] == "WAITING_REVIEW"


def _material_traceability_report() -> None:
    _, data_root, contract = _workspace()
    inbox = data_root / "inbox"
    inbox.mkdir()
    (inbox / "current.md").write_text("REQ-DEMO-01 当前需求。\n", encoding="utf-8")
    state = run_module_analysis(str(contract))
    task = read_json(Path(state["agent_task_paths"][0]))
    result = _task_result(task)
    result["evidence"].append({
        "chunk_id": "material:inbox/current.md:1-1",
        "location": "inbox/current.md:1-1",
        "observation": "REQ-DEMO-01 作为当前需求采用。",
    })
    result["analysis_checkpoint"]["material_decisions"] = [{
        "path": "inbox/current.md",
        "decision": "current",
        "reason": "当前需求基线。",
    }]
    result["test_cases"] = [_test_case(
        "U00-MAT-TC1",
        requirement_ids=["REQ-DEMO-01"],
        material_ids=["MAT:inbox/current.md"],
        title="当前需求行为验证",
    )]
    write_json(Path(task["result_path"]), result)
    run_module_analysis(str(contract))
    _review(data_root, "smoke-01", status="PASS")
    state = run_module_analysis(str(contract))
    markdown = Path(state["report_path"]).read_text(encoding="utf-8")
    html_report = Path(state["html_report_path"]).read_text(encoding="utf-8")
    for expected in ("inbox/current.md", "inbox/current.md:1-1", "REQ-DEMO-01 作为当前需求采用。", "MAT:inbox/current.md"):
        assert expected in markdown and expected in html_report
    assert "### 资料采用与排除结论" not in markdown
    assert "### 资料引用" in markdown


def _multi_repo_isolation() -> None:
    _, data_root, contract = _workspace(repositories=("repo-a", "repo-b"))
    state = run_module_analysis(str(contract))
    assert [unit["repo_id"] for unit in state["analysis_units"]] == ["repo-a", "repo-b"]
    tasks = [read_json(Path(path)) for path in state["agent_task_paths"]]
    assert len(tasks) == 2
    assert all(len(task["repositories"]) == 1 for task in tasks)
    assert {task["repositories"][0]["repo_id"] for task in tasks} == {"repo-a", "repo-b"}
    _write_all_analysis(state)
    run_module_analysis(str(contract))
    review_task = read_json(
        data_root / "runs" / "smoke-01" / "agent-tasks" / "review-independent.json"
    )
    assert {repo["repo_id"] for repo in review_task["repositories"]} == {"repo-a", "repo-b"}


def _unchanged_result_edit_does_not_block() -> None:
    _, data_root, contract = _workspace(repositories=("repo-a", "repo-b"))
    state = run_module_analysis(str(contract))
    _write_all_analysis(state)
    run_module_analysis(str(contract))
    _review(data_root, "smoke-01", status="REWORK")
    state = run_module_analysis(str(contract))
    assert state["phase"] == "WAITING_REWORK"
    run_dir = data_root / "runs" / "smoke-01"
    unchanged_path = run_dir / "agent-results" / "analysis" / "U01.json"
    original = read_json(unchanged_path)
    changed = dict(original)
    changed["summary"] = "初审后被修改"
    write_json(unchanged_path, changed)
    rework_task = read_json(run_dir / "agent-tasks" / "rework" / "U00.json")
    write_json(Path(rework_task["result_path"]), _task_result(rework_task))
    state = run_module_analysis(str(contract))
    assert state["phase"] == "WAITING_REWORK_REVIEW"


def _bounded_scope_expansion() -> None:
    _, _, contract = _workspace()
    repo = Path(read_json(contract)["data_root"]) / "repositories" / "demo"
    (repo / "module" / "demo_internal.h").write_text(
        "static inline int demo_abort(void) { assert(false); return 0; }\n"
        "static inline void demo_remove(void) { assert(STAILQ_EMPTY(&recv_stream)); }\n"
        "static inline void demo_release(void) { assert(entry->ref > 0); }\n"
        "static inline void demo_state(void) { assert(sock->pipe_has_data == false); }\n"
        "static inline void demo_mark(void) { sock->pipe_has_data = true; }\n"
        "static inline void demo_set_pipe(void) { destroy_pipe(); }\n",
        encoding="utf-8",
    )
    (repo / "module" / "unused_internal.h").write_text(
        "static inline int demo_unused(void) { return 0; }\n",
        encoding="utf-8",
    )
    (repo / "module" / "entry.c").write_text(
        '#include "demo_internal.h"\n#include "unused_internal.h"\n'
        "int demo_feature_start(void) { return demo_abort(); }\n"
        "int demo_add(void) { return spdk_sock_map_insert(); }\n"
        "void demo_drop(void) { spdk_sock_map_release(); }\n",
        encoding="utf-8",
    )
    (repo / "app").mkdir()
    (repo / "app" / "rpc.c").write_text(
        "int demo_feature_start(void);\n"
        "int rpc_start(void) { return demo_feature_start(); }\n"
        "int rpc_add(void) { return spdk_sock_map_insert(); }\n"
        "void rpc_drop(void) { spdk_sock_map_release(); }\n",
        encoding="utf-8",
    )
    (repo / "unrelated").mkdir()
    (repo / "unrelated" / "noise.c").write_text("int unrelated(void) { return 0; }\n", encoding="utf-8")
    (repo / "test" / "e2e").mkdir(parents=True)
    (repo / "test" / "e2e" / "demo.sh").write_text("demo_feature_start()\n", encoding="utf-8")
    payload = read_json(contract)
    payload["target"] = "demo feature"
    payload["source_scope"] = ["module/entry.c"]
    write_json(contract, payload)
    state = run_module_analysis(str(contract))
    task = read_json(Path(state["agent_task_paths"][0]))
    assert "module/entry.c" in task["unit"]["source_scope"]
    assert "module/demo_internal.h" in task["unit"]["context_scope"]
    assert "module/unused_internal.h" not in task["unit"]["context_scope"]
    assert task["failure_signal_context"] == [
        {
            "path": "module/demo_internal.h",
            "line": 1,
            "signal": "static inline int demo_abort(void) { assert(false); return 0; }",
            "analysis_focus": (
                "先定位直接支配 assert 的失败条件，再分别重放 Debug 与 Release。受支持模式中的底层"
                "操作若可返回失败，且公开契约或入口没有阻断，Debug 终止必须保留为风险；不能用"
                " assert 后的清理或返回排除，Release 继续核对清理后的最终状态。条件含数值句柄时，"
                "必须从创建函数的失败返回值确认哨兵，并把 0 作为独立边界重放。"
            ),
            "related_state_context": [],
        },
        {
            "path": "module/demo_internal.h",
            "line": 2,
            "signal": "static inline void demo_remove(void) { assert(STAILQ_EMPTY(&recv_stream)); }",
            "analysis_focus": (
                "追踪容器元素的产生、归还和公开移除入口；实现注释或 assert 本身不是调用方契约，"
                "只有公开契约或入口强制检查才能证明该状态不可达。"
            ),
            "related_state_context": [],
        },
        {
            "path": "module/demo_internal.h",
            "line": 3,
            "signal": "static inline void demo_release(void) { assert(entry->ref > 0); }",
            "analysis_focus": (
                "按任务提供的每个直接实现写出实际的增加与减少调用序列，并追踪错误日志之后的函数"
                "返回值和上层是否真正绑定对象。只有证明某次 release/decrement 前没有成功 insert/"
                "increment 才能判定失衡；lookup 不增加引用本身不足以证明风险。一个实现的结论不能"
                "覆盖另一个实现。"
            ),
            "related_state_context": [],
        },
        {
            "path": "module/demo_internal.h",
            "line": 4,
            "signal": "static inline void demo_state(void) { assert(sock->pipe_has_data == false); }",
            "analysis_focus": (
                "把断言可达性与重配置后的状态残留拆成两条 failure path。先判断断言本身，再从状态置位"
                "重放 related_state_context 中的 destroy/NULL/setter；即使断言不可达，重配置仍可能独立"
                "造成数据丢失或残留状态。当前分支没有写入者不能证明先前状态不会残留。"
            ),
            "related_state_context": [
                "module/demo_internal.h:5: static inline void demo_mark(void) { sock->pipe_has_data = true; }",
                "module/demo_internal.h:6: static inline void demo_set_pipe(void) { destroy_pipe(); }",
            ],
        },
    ]
    assert "app/rpc.c" in task["unit"]["context_scope"]
    assert "unrelated/noise.c" not in task["unit"]["source_scope"]
    assert "test/e2e/demo.sh" in task["unit"]["context_scope"]
    assert len(task["unit"]["context_scope"]) <= 10
    assert task["max_parallel_workers"] == 4 and task["may_spawn_workers"] is False
    semantic_checks = task["semantic_check_items"]
    semantic_kinds = [item["kind"] for item in semantic_checks]
    assert semantic_kinds == [
        "assertion_reachability",
        "assertion_reachability",
        "paired_operation",
        "paired_operation",
        "assertion_reachability",
        "resource_reconfiguration",
        "resource_reconfiguration",
    ], semantic_kinds
    assert [item["subject_path"] for item in semantic_checks[2:4]] == [
        "app/rpc.c",
        "module/entry.c",
    ]
    assert len({item["check_id"] for item in semantic_checks}) == len(semantic_checks)


def _state_context_balances_lifecycle_and_reconfiguration() -> None:
    lines = [
        *(f"sock->pending_recv = {'true' if index % 2 else 'false'};" for index in range(8)),
        *(f"set_recv_pipe_{index}();" for index in range(8)),
    ]
    context = _related_state_context(
        "module/demo.c", lines, "assert(sock->pending_recv == false);"
    )
    assert len(context) == 12
    assert context[:3] == [
        "module/demo.c:1: sock->pending_recv = false;",
        "module/demo.c:2: sock->pending_recv = true;",
        "module/demo.c:3: sock->pending_recv = false;",
    ]
    assert context[3:6] == [
        "module/demo.c:6: sock->pending_recv = true;",
        "module/demo.c:7: sock->pending_recv = false;",
        "module/demo.c:8: sock->pending_recv = true;",
    ]
    assert context[6] == "module/demo.c:9: set_recv_pipe_0();"
    assert context[-1] == "module/demo.c:16: set_recv_pipe_7();"


def _expected_behavior_not_risk() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    task = read_json(Path(state["agent_task_paths"][0]))
    result = _task_result(task)
    result["risks"] = [{
        "risk_id": "R-EXPECTED",
        "title": "规格已经定义的行为",
        "affected_paths": task["unit"]["source_scope"],
        "dfx": ["功能与状态"],
        "severity": "Low",
        "confidence": "high",
        "trigger": "触发条件",
        "system_result": "规格行为",
        "external_observation": "可观测",
        "exclusion_condition": "无",
        "upstream_semantics": {
            "reachability": "可达",
            "caller_constraints": "调用方按规格处理",
            "documented_behavior": "高层 API 明确定义为预期行为",
            "existing_tests": "已有测试验证该规格行为",
            "conclusion": "expected_behavior",
        },
        "translation_status": "Blackbox-ready",
        "status": "pending",
        "evidence": result["evidence"],
    }]
    result["test_cases"] = [_test_case("TC-EXPECTED", risk_ids=["R-EXPECTED"])]
    write_json(Path(task["result_path"]), result)
    assert run_module_analysis(str(contract))["phase"] == "WAITING_REVIEW"
    assert not read_json(data_root / "runs" / "smoke-01" / "progress.json")["errors"]


def _unversioned_source_is_sample() -> None:
    _, data_root, contract = _workspace()
    shutil.rmtree(data_root / "repositories" / "demo" / ".git")
    state = run_module_analysis(str(contract))
    _write_all_analysis(state)
    run_module_analysis(str(contract))
    _review(data_root, "smoke-01", status="PASS")
    state = run_module_analysis(str(contract))
    assert state["phase"] == "INCOMPLETE"
    assert any(
        item.get("kind") == "source_version_unverifiable"
        for item in state["quality_report"]["unresolved"]
    )


def _known_c_macro_parse_artifacts() -> None:
    assert _known_macro_parse_artifact(
        {"line": 1, "text": ","}, ["TAILQ_HEAD(, item) head;"]
    )
    assert _known_macro_parse_artifact(
        {"line": 2, "text": "cc."}, ["return offsetof(struct registers,", "cc.raw);"]
    )
    assert _known_macro_parse_artifact(
        {"line": 2, "text": "struct"}, ["ctx =", "SPDK_CONTAINEROF(arg, struct ctx, member);"]
    )
    assert _known_macro_parse_artifact(
        {"line": 1, "text": "hex_char"}, ["static const char __spdk_nonstring hex_char[16];"]
    )
    assert _known_macro_parse_artifact(
        {"line": 3, "text": "void"},
        ["SPDK_LOG_REGISTER_COMPONENT(demo)", "", "static void"],
    )
    assert not _known_macro_parse_artifact(
        {"line": 1, "text": "unexpected"}, ["int broken = ;"]
    )


def _source_checkpoint_uses_frozen_inputs() -> None:
    _, data_root, contract = _workspace()
    raw_contract = read_json(contract)
    state = load_contract({"run_id": "smoke-01", "data_root": str(data_root), "task_contract": raw_contract})
    state = locate_module(resolve_repositories(state))
    progress = read_json(data_root / "runs" / "smoke-01" / "progress.json")
    assert progress["init_step"] == "SOURCE_READY"
    original = data_root / "repositories" / "demo" / "module" / "entry.c"
    original.write_text("int changed_after_checkpoint(void) { return 1; }\n", encoding="utf-8")
    state = run_module_analysis(str(contract))
    task = read_json(Path(state["agent_task_paths"][0]))
    frozen = Path(task["repositories"][0]["source_root"]) / "module" / "entry.c"
    assert "demo_start" in frozen.read_text(encoding="utf-8")
    assert "changed_after_checkpoint" not in frozen.read_text(encoding="utf-8")


def _index_checkpoint_resumes_without_live_source() -> None:
    _, data_root, contract = _workspace()
    raw_contract = read_json(contract)
    state = load_contract({"run_id": "smoke-01", "data_root": str(data_root), "task_contract": raw_contract})
    state = locate_module(resolve_repositories(state))
    index_materials(state)
    progress = read_json(data_root / "runs" / "smoke-01" / "progress.json")
    assert progress["init_step"] == "INDEX_READY"
    (data_root / "repositories" / "demo" / "module" / "entry.c").unlink()
    state = run_module_analysis(str(contract))
    assert state["phase"] == "WAITING_ANALYSIS"
    assert Path(state["agent_task_paths"][0]).is_file()


def _agent_start_checkpoint() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    task_path = Path(state["agent_task_paths"][0])
    subprocess.run(
        [sys.executable, "-m", "pangea_agent.cli.main", "prepare-worker-result", "--task", str(task_path)],
        check=True,
        capture_output=True,
    )
    progress_path = data_root / "runs" / "smoke-01" / "progress.json"
    assert read_json(progress_path)["agent_sessions"]["analysis:U00"]["status"] == "dispatched"
    task = read_json(task_path)
    write_json(Path(task["result_path"]), _task_result(task))
    state = run_module_analysis(str(contract))
    review_path = data_root / "runs" / "smoke-01" / "agent-tasks" / "review-independent.json"
    subprocess.run(
        [sys.executable, "-m", "pangea_agent.cli.main", "prepare-review-result", "--task", str(review_path)],
        check=True,
        capture_output=True,
    )
    progress = read_json(progress_path)
    assert state["phase"] == "WAITING_REVIEW"
    assert progress["agent_sessions"]["review"]["status"] == "dispatched"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pangea_agent.cli.main",
            "record-agent-session",
            "--run-id",
            "smoke-01",
            "--data-root",
            str(data_root),
            "--role",
            "review",
            "--task-id",
            "review-task-1",
        ],
        check=True,
        capture_output=True,
    )
    _advance_to_review_comparison(data_root, "smoke-01")
    progress = read_json(progress_path)
    assert progress["agent_sessions"]["review"] == {
        "role": "review",
        "unit_id": None,
        "stage": "comparison_review",
        "task_id": "review-task-1",
        "status": "pending",
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pangea_agent.cli.main",
            "prepare-review-result",
            "--task",
            str(data_root / "runs" / "smoke-01" / "agent-tasks" / "review.json"),
        ],
        check=True,
        capture_output=True,
    )
    assert read_json(progress_path)["agent_sessions"]["review"]["status"] == "dispatched"


def _legacy_task_uses_c_cpp_defaults() -> None:
    _, _, contract = _workspace()
    state = run_module_analysis(str(contract))
    task_path = Path(state["agent_task_paths"][0])
    payload = read_json(task_path)
    payload["unit"].pop("languages", None)
    payload["unit"].pop("frameworks", None)
    payload.pop("checkpoint_rubric_paths", None)
    write_json(task_path, payload)

    task = load_worker_task(task_path)
    assert task.unit.languages == ["c_cpp"]
    assert task.unit.frameworks == []
    assert task.checkpoint_rubric_paths == [
        "src/pangea_agent/rubrics/builtin/c_cpp_analysis.md"
    ]


def _lua_openubmc_task_metadata() -> None:
    _, _, contract = _lua_workspace()
    state = run_module_analysis(str(contract))
    task = read_json(Path(state["agent_task_paths"][0]))

    assert task["unit"]["languages"] == ["lua"]
    assert task["unit"]["frameworks"] == ["openubmc"]
    assert task["unit"]["source_scope"] == ["module/component.lua"]
    assert "module/helper.lua" in task["unit"]["context_scope"]
    assert task["checkpoint_rubric_paths"] == [
        "src/pangea_agent/rubrics/builtin/lua_analysis.md",
        "src/pangea_agent/rubrics/builtin/openubmc_analysis.md",
    ]
    check_ids = {item["check_id"] for item in task["semantic_check_items"]}
    assert any(value.startswith("SC-LUA-ERROR-") for value in check_ids)
    assert any(value.startswith("SC-OPENUBMC-LIFECYCLE-") for value in check_ids)
    assert any(value.startswith("SC-OPENUBMC-SIGNAL-") for value in check_ids)
    assert any(value.startswith("SC-LUA-REQUIRE-") for value in check_ids)


def _lua_direct_dependency_keeps_context_boundary_and_framework_checks() -> None:
    _, data_root, contract = _lua_workspace()
    repo = data_root / "repositories" / "lua-demo"
    (repo / "module" / "entry.lua").write_text(
        'return require("module.component")\n', encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(repo), "add", "module/entry.lua"], check=True)
    subprocess.run(
        [
            "git", "-C", str(repo), "-c", "user.name=PANGEA Smoke",
            "-c", "user.email=pangea-smoke@example.invalid", "commit", "-qm", "lua entry fixture",
        ],
        check=True,
    )
    payload = read_json(contract)
    payload["source_scope"] = ["module/entry.lua"]
    write_json(contract, payload)

    state = run_module_analysis(str(contract))
    task = read_json(Path(state["agent_task_paths"][0]))
    assert task["unit"]["source_scope"] == ["module/entry.lua"]
    assert "module/component.lua" in task["unit"]["context_scope"]
    assert task["unit"]["frameworks"] == ["openubmc"]
    assert "src/pangea_agent/rubrics/builtin/openubmc_analysis.md" in task["checkpoint_rubric_paths"]
    component_checks = [
        item for item in task["semantic_check_items"]
        if item["subject_path"] == "module/component.lua"
    ]
    assert any(item["check_id"].startswith("SC-OPENUBMC-LIFECYCLE-") for item in component_checks)
    assert any(item["check_id"].startswith("SC-OPENUBMC-SIGNAL-") for item in component_checks)
    entry_inventory = next(
        item for item in state["inventory"]["files"]
        if item["repo_id"] == "lua-demo" and item["path"] == "module/entry.lua"
    )
    assert entry_inventory["imports"][0]["resolved_path"] == "module/component.lua"
    markdown = render_report(state)
    assert "| 源码文件数 | 1 |" in markdown
    assert "| Lua 文件数 | 1 |" in markdown
    assert "覆盖 1 个源码文件（C/C++ 0，Lua 1）和 1 个上游语义文件" in markdown


def _lua_parser_binds_direct_assignments_and_self_signals() -> None:
    root = Path(tempfile.mkdtemp(prefix="pangea-lua-symbols-"))
    _TEMP_ROOTS.append(root)
    path = root / "component.lua"
    path.write_text(
        """local ignored, Component = make(), mc.class("Component")
local Wrapped = decorate(mc.class("Wrapped"))
Component.changed = mc.signal()
function Component:init() self.changed:connect(function() end) end
function Component:run() self.changed:emit() end
""",
        encoding="utf-8",
    )
    parsed = parse_lua_file(path)
    declarations = [
        item["symbol"] for item in parsed["framework_signals"]
        if item["kind"] == "class_declaration"
    ]
    assert "Component" in declarations
    assert "ignored" not in declarations and "Wrapped" not in declarations
    signal_kinds = {item["kind"] for item in parsed["framework_signals"]}
    assert {"signal_callback", "signal_emit"} <= signal_kinds

    scoped_path = root / "scoped.lua"
    scoped_path.write_text(
        """function A()
    local changed = mc.signal()
    changed:emit()
end
function B()
    changed:emit()
end
local M = { run = function() return true end }
return M
""",
        encoding="utf-8",
    )
    scoped = parse_lua_file(scoped_path)
    emits = [item for item in scoped["framework_signals"] if item["kind"] == "signal_emit"]
    assert len(emits) == 1
    assert any(item["symbol"] == "M.run" for item in scoped["functions"])


def _lua_coverage_path_disambiguates_duplicate_symbols() -> None:
    inventory = {
        "files": [
            {
                "repo_id": "lua-demo",
                "path": "module/a.lua",
                "language": "lua",
                "functions": [{"symbol": "Alpha:init", "line": 2}],
            },
            {
                "repo_id": "lua-demo",
                "path": "module/b.lua",
                "language": "lua",
                "functions": [{"symbol": "Beta:init", "line": 4}],
            },
        ]
    }
    base = {"coverage_type": "function", "module": "", "function": "init", "count": 0}
    without_path = match_coverage_records([base], inventory)
    assert len(without_path["unmatched"]) == 1

    matched = match_coverage_records([{**base, "path": "module/b.lua"}], inventory)
    assert matched["matched"][0]["matches"] == [
        {"repo_id": "lua-demo", "path": "module/b.lua", "line": 4}
    ]

    unmatched = match_coverage_records([{**base, "path": "module/missing.lua"}], inventory)
    assert len(unmatched["unmatched"]) == 1
    assert not unmatched["matched"]
    dotted = match_coverage_records([
        {**base, "function": "Beta.init"}
    ], inventory)
    assert dotted["matched"][0]["matches"][0]["line"] == 4


def _coverage_ignores_context_symbol_collisions() -> None:
    unit = AnalysisUnit(
        unit_id="U00",
        repo_id="lua-demo",
        title="Lua source",
        source_scope=["module/source.lua"],
        context_scope=["module/context.lua"],
        focus=["test_cases"],
        dfx=["功能与状态"],
        languages=["lua"],
    )
    inventory = {
        "files": [
            {
                "repo_id": "lua-demo",
                "path": "module/source.lua",
                "language": "lua",
                "functions": [{"symbol": "Component:init", "line": 3}],
            },
            {
                "repo_id": "lua-demo",
                "path": "module/context.lua",
                "language": "lua",
                "functions": [{"symbol": "Component:init", "line": 8}],
            },
        ],
        "file_count": 2,
    }
    records = [{
        "coverage_type": "function",
        "module": "component",
        "function": "Component.init",
        "count": 0,
    }]
    report = match_coverage_records(records, _source_inventory(unit, inventory))
    context = _coverage_context(unit, report)
    assert len(report["matched"]) == 1 and not report["ambiguous"]
    assert context[0]["path"] == "module/source.lua"


def _coverage_context_collision_keeps_source_gap_through_advance() -> None:
    _, data_root, contract = _lua_workspace()
    repo = data_root / "repositories" / "lua-demo"
    (repo / "module" / "entry.lua").write_text(
        """local Context = require("module.component")
local Component = {}
function Component:init() return Context ~= nil end
return Component
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "module/entry.lua"], check=True)
    subprocess.run(
        [
            "git", "-C", str(repo), "-c", "user.name=PANGEA Smoke",
            "-c", "user.email=pangea-smoke@example.invalid", "commit", "-qm", "coverage collision fixture",
        ],
        check=True,
    )
    payload = read_json(contract)
    payload["source_scope"] = ["module/entry.lua"]
    write_json(contract, payload)
    coverage = data_root / "coverage"
    coverage.mkdir()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["module", "function", "count"])
    sheet.append(["component", "Component.init", 0])
    workbook.save(coverage / "collision.xlsx")
    workbook.close()

    state = run_module_analysis(str(contract))
    assert len(state["coverage_report"]["matched"]) == 1
    assert not state["coverage_report"]["ambiguous"]
    task = read_json(Path(state["agent_task_paths"][0]))
    gap_id = task["coverage_context"][0]["gaps"][0]["coverage_id"]
    assert task["coverage_context"][0]["path"] == "module/entry.lua"
    result = _task_result(task)
    result["test_cases"] = [_test_case("U00-COV-TC1", coverage_ids=[gap_id])]
    result["analysis_checkpoint"]["coverage_decisions"] = [{
        "coverage_id": gap_id,
        "disposition": "new_coverage_case",
        "linked_test_case_ids": ["U00-COV-TC1"],
        "reason": "从当前源码入口补齐未执行函数。",
    }]
    write_json(Path(task["result_path"]), result)
    state = run_module_analysis(str(contract))
    assert state["phase"] == "WAITING_REVIEW"
    assert len(state["coverage_report"]["matched"]) == 1
    assert not state["coverage_report"]["ambiguous"]


def _lua_context_inventory_isolated_by_repository() -> None:
    _, data_root, contract = _workspace(repositories=("repo-a", "repo-b"))
    for repo_id in ("repo-a", "repo-b"):
        repo = data_root / "repositories" / repo_id
        (repo / "module" / "entry.c").unlink()
        (repo / "module" / "component.lua").write_text(
            "local C = mc.class(\"C\")\nfunction C:init() end\nreturn C\n",
            encoding="utf-8",
        )
        entry = (
            'return require("module.component")\n'
            if repo_id == "repo-a"
            else "return { value = true }\n"
        )
        (repo / "module" / "entry.lua").write_text(entry, encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(
            [
                "git", "-C", str(repo), "-c", "user.name=PANGEA Smoke",
                "-c", "user.email=pangea-smoke@example.invalid", "commit", "-qm", "lua repo fixture",
            ],
            check=True,
        )
    payload = read_json(contract)
    payload["source_scope"] = ["module/entry.lua"]
    write_json(contract, payload)
    state = run_module_analysis(str(contract))
    inventory_paths = {
        (item["repo_id"], item["path"]) for item in state["inventory"]["files"]
    }
    assert ("repo-a", "module/component.lua") in inventory_paths
    assert ("repo-b", "module/component.lua") not in inventory_paths


def _coverage_workbook_preserves_source_path() -> None:
    root = Path(tempfile.mkdtemp(prefix="pangea-coverage-path-"))
    _TEMP_ROOTS.append(root)
    workbook = Workbook()
    function_sheet = workbook.active
    function_sheet.title = "functions"
    function_sheet.append(["module", "path", "function", "count"])
    function_sheet.append(["power", "module/component.lua", "init", 0])
    branch_sheet = workbook.create_sheet("branches")
    branch_sheet.append([
        "branch_id", "路径", "function", "condition", "true_count", "false_count"
    ])
    branch_sheet.append(["B1", "module/component.lua", "run", "ready", 0, 2])
    path = root / "coverage.xlsx"
    workbook.save(path)
    workbook.close()

    records, warnings = parse_coverage_xlsx(path)
    assert warnings == []
    assert [item["path"] for item in records] == [
        "module/component.lua", "module/component.lua"
    ]


def _mixed_language_reports_show_frameworks() -> None:
    _, _, contract = _workspace()
    state = run_module_analysis(str(contract))
    state["inventory"]["files"].append({
        "repo_id": "demo",
        "path": "module/component.lua",
        "language": "lua",
        "frameworks": ["openubmc"],
    })
    state["inventory"]["file_count"] += 1
    state["analysis_units"][0]["source_scope"].append("module/component.lua")
    state["analysis_units"][0]["languages"] = ["c_cpp", "lua"]
    state["analysis_units"][0]["frameworks"] = ["openubmc"]

    markdown = render_report(state)
    html = render_html_report(state)
    assert "| C/C++ 文件数 | 1 |" in markdown
    assert "| Lua 文件数 | 1 |" in markdown
    assert "| 识别框架 | openubmc |" in markdown
    assert "<td>C/C++ 文件数</td><td>1</td>" in html
    assert "<td>Lua 文件数</td><td>1</td>" in html
    assert "<td>识别框架</td><td>openubmc</td>" in html

    legacy_state = run_module_analysis(str(contract))
    for item in legacy_state["inventory"]["files"]:
        item.pop("language", None)
    legacy_markdown = render_report(legacy_state)
    assert "| C/C++ 文件数 | 1 |" in legacy_markdown


SCENARIOS: tuple[tuple[str, Scenario], ...] = (
    ("PASS 到双报告", _pass_report),
    ("reviewer 发现遗漏时不能 PASS", _review_missing_finding_cannot_pass),
    ("对照复核不能丢弃独立 finding", _comparison_cannot_drop_independent_findings),
    ("REWORK 同 reviewer 通过", _rework_same_reviewer),
    ("原 reviewer 无法恢复时显式 UNRESOLVED", _reviewer_unavailable_has_explicit_unresolved_path),
    ("截断结果覆盖修正", _truncated_correction),
    ("黑盒步骤与预期必须逐项对应", _mismatched_step_results_rejected),
    ("需求补测无需伪造风险关联", _requirement_only_test_case_is_accepted),
    ("semantic check 约束风险实现范围", _semantic_check_risk_scope_is_enforced),
    ("无法关联证据标记待确认", _unmatched_evidence_is_pending),
    ("跨单元重复 ID 自动修正", _duplicate_ids_correction),
    ("机械路径变化自动修正", _mechanical_task_change_does_not_block),
    ("review 机械字段自动修正", _mechanical_review_fields_do_not_block),
    ("不存在 scope 拒绝", _missing_scope),
    ("终态报告恢复", _report_recovery),
    ("文档缺口强制不完整", _document_gap),
    ("有 Coverage 记录但无缺口时不强制补测", _coverage_reference_only),
    ("Coverage 缺口必须闭环到用例", _coverage_gap_requires_test_case),
    ("相关资料必须闭环且报告不展示排除章节", _material_traceability_report),
    ("多 repo 单元隔离", _multi_repo_isolation),
    ("返工期间未返工结果编辑不阻塞", _unchanged_result_edit_does_not_block),
    ("范围只扩到直接调用与相关上下文", _bounded_scope_expansion),
    ("状态上下文均衡保留生命周期与重配置", _state_context_balances_lifecycle_and_reconfiguration),
    ("预期行为不能列为风险", _expected_behavior_not_risk),
    ("无法确认源码版本时只出样本报告", _unversioned_source_is_sample),
    ("SOURCE_READY 恢复只使用冻结输入", _source_checkpoint_uses_frozen_inputs),
    ("INDEX_READY 恢复不再读取活动源码", _index_checkpoint_resumes_without_live_source),
    ("Agent 启动状态写入 Run checkpoint", _agent_start_checkpoint),
    ("已知 C 宏解析误报不冒充真实缺口", _known_c_macro_parse_artifacts),
    ("旧 WorkerTask 使用 C/C++ 默认规则", _legacy_task_uses_c_cpp_defaults),
    ("Lua openUBMC task 冻结语言与规则", _lua_openubmc_task_metadata),
    ("Lua 直接依赖保留 context 边界并生成框架检查", _lua_direct_dependency_keeps_context_boundary_and_framework_checks),
    ("Lua parser 正确绑定多赋值与 self signal", _lua_parser_binds_direct_assignments_and_self_signals),
    ("Lua 重名函数 Coverage 使用路径消歧", _lua_coverage_path_disambiguates_duplicate_symbols),
    ("Coverage 匹配忽略 context 重名符号", _coverage_ignores_context_symbol_collisions),
    ("Coverage source 缺口推进后仍保持匹配", _coverage_context_collision_keeps_source_gap_through_advance),
    ("Lua context inventory 按源码仓隔离", _lua_context_inventory_isolated_by_repository),
    ("Coverage Excel 保留源码路径", _coverage_workbook_preserves_source_path),
    ("混合语言报告显示框架", _mixed_language_reports_show_frameworks),
)


def main() -> None:
    logging.getLogger("pypdf").setLevel(logging.CRITICAL)
    for name, scenario in SCENARIOS:
        scenario()
        print(f"PASS  {name}")
    print(f"PASS  V1 smoke ({len(SCENARIOS)} scenarios)")


if __name__ == "__main__":
    main()
