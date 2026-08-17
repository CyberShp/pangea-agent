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
from pangea_agent.graph.nodes.advance_run import advance_run
from pangea_agent.graph.nodes.load_contract import load_contract
from pangea_agent.graph.nodes.resolve_repositories import resolve_repositories
from pangea_agent.graph.nodes.locate_module import locate_module
from pangea_agent.graph.nodes.index_materials import index_materials
from pangea_agent.inventory.source_scanner import _known_macro_parse_artifact


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


def _task_result(task: dict, *, finish_reason: str = "stop", fake_location: bool = False) -> dict:
    repo_id = task["unit"]["repo_id"]
    chunk_id = f"{repo_id}:module/entry.c:1-1"
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
                "path_id": "F-001",
                "trigger": "入口调用",
                "side_effects": "进入模块逻辑",
                "failure": "无已确认故障",
                "caller_handling": "调用方读取返回值",
                "final_states": "模块保持可用",
                "disposition": "excluded",
            }],
            "material_decisions": [],
            "coverage_priorities": [],
            "risk_set_frozen": True,
            "counterexamples_checked": ["异常返回不会被误写为成功"],
        },
    }


def _mismatched_step_results_rejected() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    task = read_json(Path(state["agent_task_paths"][0]))
    result = _task_result(task)
    result["risks"] = [{
        "risk_id": "U00-R1",
        "title": "可执行风险",
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


def _write_all_analysis(state: dict) -> None:
    for task_path in state["agent_task_paths"]:
        task = read_json(Path(task_path))
        write_json(Path(task["result_path"]), _task_result(task))


def _review(data_root: Path, run_id: str, *, status: str, reviewer: str = "reviewer-1") -> None:
    task = read_json(data_root / "runs" / run_id / "agent-tasks" / "review.json")
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
        "independent_findings": [],
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
    html = Path(state["html_report_path"]).read_text(encoding="utf-8")
    assert markdown.startswith("# CHAP 分析报告")
    assert "<title>CHAP 分析报告</title>" in html
    assert "PANGEA Agent 测试分析报告" not in markdown + html
    assert "COMPLETE / PASS" not in markdown + html
    assert "| 项目 | 内容 |" in markdown
    assert "| 类别 | 源码仓 | 路径 | 纳入原因 |" in markdown
    assert "质量门禁已通过。完成" in markdown and "质量门禁已通过。完成" in html


def _rework_same_reviewer() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    _write_all_analysis(state)
    run_module_analysis(str(contract))
    _review(data_root, "smoke-01", status="REWORK", reviewer="reviewer-1")
    state = run_module_analysis(str(contract))
    assert state["phase"] == "WAITING_REWORK"
    rework_task = read_json(data_root / "runs" / "smoke-01" / "agent-tasks" / "rework" / "U00.json")
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
        write_json(Path(task["result_path"]), result)
    assert run_module_analysis(str(contract))["phase"] == "WAITING_REVIEW"
    normalized = [read_json(Path(task["result_path"])) for task in tasks]
    assert {item["risks"][0]["risk_id"] for item in normalized} == {"DUP-R01", "DUP-R01-2"}
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
        "independent_findings": [],
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
        assert "未发现对应 C/C++ 实现" in str(exc)
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
    write_json(Path(task["result_path"]), result)
    run_module_analysis(str(contract))
    _review(data_root, "smoke-01", status="PASS")
    state = run_module_analysis(str(contract))
    markdown = Path(state["report_path"]).read_text(encoding="utf-8")
    html = Path(state["html_report_path"]).read_text(encoding="utf-8")
    for expected in ("inbox/current.md", "inbox/current.md:1-1", "REQ-DEMO-01 作为当前需求采用。"):
        assert expected in markdown and expected in html
    assert "### 资料采用与排除结论" in markdown
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
    review_task = read_json(data_root / "runs" / "smoke-01" / "agent-tasks" / "review.json")
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
        "static inline int demo_abort(void) { assert(false); return 0; }\n",
        encoding="utf-8",
    )
    (repo / "module" / "unused_internal.h").write_text(
        "static inline int demo_unused(void) { return 0; }\n",
        encoding="utf-8",
    )
    (repo / "module" / "entry.c").write_text(
        '#include "demo_internal.h"\n#include "unused_internal.h"\nint demo_feature_start(void) { return demo_abort(); }\n',
        encoding="utf-8",
    )
    (repo / "app").mkdir()
    (repo / "app" / "rpc.c").write_text(
        "int demo_feature_start(void);\nint rpc_start(void) { return demo_feature_start(); }\n",
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
    assert task["failure_signal_context"] == [{
        "path": "module/demo_internal.h",
        "line": 1,
        "signal": "static inline int demo_abort(void) { assert(false); return 0; }",
    }]
    assert "app/rpc.c" in task["unit"]["context_scope"]
    assert "unrelated/noise.c" not in task["unit"]["source_scope"]
    assert "test/e2e/demo.sh" in task["unit"]["context_scope"]
    assert len(task["unit"]["context_scope"]) <= 10
    assert task["max_parallel_workers"] == 4 and task["may_spawn_workers"] is False


def _expected_behavior_not_risk() -> None:
    _, data_root, contract = _workspace()
    state = run_module_analysis(str(contract))
    task = read_json(Path(state["agent_task_paths"][0]))
    result = _task_result(task)
    result["risks"] = [{
        "risk_id": "R-EXPECTED",
        "title": "规格已经定义的行为",
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
    review_path = data_root / "runs" / "smoke-01" / "agent-tasks" / "review.json"
    subprocess.run(
        [sys.executable, "-m", "pangea_agent.cli.main", "prepare-review-result", "--task", str(review_path)],
        check=True,
        capture_output=True,
    )
    progress = read_json(progress_path)
    assert state["phase"] == "WAITING_REVIEW"
    assert progress["agent_sessions"]["review"]["status"] == "dispatched"


SCENARIOS: tuple[tuple[str, Scenario], ...] = (
    ("PASS 到双报告", _pass_report),
    ("REWORK 同 reviewer 通过", _rework_same_reviewer),
    ("截断结果覆盖修正", _truncated_correction),
    ("黑盒步骤与预期必须逐项对应", _mismatched_step_results_rejected),
    ("无法关联证据标记待确认", _unmatched_evidence_is_pending),
    ("跨单元重复 ID 自动修正", _duplicate_ids_correction),
    ("机械路径变化自动修正", _mechanical_task_change_does_not_block),
    ("review 机械字段自动修正", _mechanical_review_fields_do_not_block),
    ("不存在 scope 拒绝", _missing_scope),
    ("终态报告恢复", _report_recovery),
    ("文档缺口强制不完整", _document_gap),
    ("coverage 仅函数执行线索", _coverage_reference_only),
    ("报告展示逐资料决策与引用", _material_traceability_report),
    ("多 repo 单元隔离", _multi_repo_isolation),
    ("返工期间未返工结果编辑不阻塞", _unchanged_result_edit_does_not_block),
    ("范围只扩到直接调用与相关上下文", _bounded_scope_expansion),
    ("预期行为不能列为风险", _expected_behavior_not_risk),
    ("无法确认源码版本时只出样本报告", _unversioned_source_is_sample),
    ("SOURCE_READY 恢复只使用冻结输入", _source_checkpoint_uses_frozen_inputs),
    ("INDEX_READY 恢复不再读取活动源码", _index_checkpoint_resumes_without_live_source),
    ("Agent 启动状态写入 Run checkpoint", _agent_start_checkpoint),
    ("已知 C 宏解析误报不冒充真实缺口", _known_c_macro_parse_artifacts),
)


def main() -> None:
    logging.getLogger("pypdf").setLevel(logging.CRITICAL)
    for name, scenario in SCENARIOS:
        scenario()
        print(f"PASS  {name}")
    print(f"PASS  V1 smoke ({len(SCENARIOS)} scenarios)")


if __name__ == "__main__":
    main()
