"""Versioned scene configuration. Content decisions belong to workers, not this module."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from pangea_agent.agent_io import read_json, write_json
from pangea_agent.risk_schema import RISK_FORMAT, RISK_FORMAT_V2
from pangea_agent.test_case_schema import TEST_CASE_FORMAT

PROFILE = "behavior-test-v2"
RISK_METHOD = "risk-method-v1"
SCENES = {
    "module-analysis": ("模块分析", "lightweight", "optional", "模块综合分析：业务分支、轻量风险及可选覆盖补测。"),
    "risk-analysis": ("风险分析", "primary", "optional", "聚焦有依据的失效风险及验证用例。"),
    "branch-analysis": ("分支分析", "none", "optional", "聚焦正常、边界、异常与恢复路径的测试设计。"),
    "coverage-analysis": ("覆盖率分析", "none", "required", "依据真实覆盖数据定位缺口并设计补测。"),
}


def scene_spec(scenario: str) -> dict:
    label, risk, coverage, description = SCENES[scenario]
    name = scenario.split("-")[0]
    spec = {
        "format_version": "analysis-scene-v1", "id": scenario, "label": label,
        "profile": PROFILE, "description": description, "risk_responsibility": risk,
        "coverage_requirement": coverage, "coverage_sources": ["query", "file", "asset"],
        "flow_delivery": "textual-first", "manual_diagrams": True,
        "analysis_rubrics": ["scene_common_generation", "scene_flow", f"scene_{name}"],
        "review_rubrics": ["scene_common_review", "scene_flow", f"scene_{name}"],
        "planning_rubrics": ["scene_planning"],
        "record_contracts": {"risk": RISK_FORMAT, "test_case": TEST_CASE_FORMAT},
        "presentation": {"risks": risk != "none", "coverage": coverage, "textual_flow": True},
    }
    if scenario == 'risk-analysis':
        spec.update(method_version=RISK_METHOD, risk_signals=True,
                    record_contracts={'risk': RISK_FORMAT_V2, 'check': 'behavior-check-v1', 'review_check': 'behavior-review-check-v1', 'test_case': TEST_CASE_FORMAT},
                    language_rubrics={role: {'c_cpp': ['c_cpp_risk_methods'], 'lua': ['lua_risk_methods']} for role in ('analysis', 'review')})
        spec['review_rubrics'].append('scene_risk_review')
        spec['presentation'].update(check_ledger=True, blackbox_target=False)
    return spec


def scene_options() -> dict:
    return {"scenarios": list(SCENES), "modes": ["depth", "speed"],
            "coverage_asset_input": True,
            "scenario_descriptors": [scene_spec(name) for name in SCENES]}


def frozen_scene(run_dir: Path, contract: dict) -> dict | None:
    if contract.get("analysis_profile") != PROFILE:
        return None
    spec = read_json(run_dir / "inputs" / "analysis-scene.json")
    if (spec.get("format_version") != "analysis-scene-v1" or spec.get("profile") != PROFILE
            or spec.get("id") != (contract.get("analysis_settings") or {}).get("scenario")):
        raise ValueError("冻结分析场景与当前 Run 合同不一致")
    return spec


def freeze_scene(run_dir: Path, contract: dict) -> dict | None:
    if contract.get("analysis_profile") != PROFILE:
        return None
    path = run_dir / "inputs" / "analysis-scene.json"
    if path.is_file():
        return frozen_scene(run_dir, contract)
    spec = deepcopy(scene_spec(contract["analysis_settings"]["scenario"]))
    write_json(path, spec)
    return spec


def scene_task_inputs(run_dir: Path, spec: dict | None) -> list[dict]:
    if spec is None:
        return []
    return [{"input_id": key, "path": str(run_dir / "inputs" / filename), "label": label}
            for key, filename, label in [
                ("analysis_scene", "analysis-scene.json", "冻结分析场景与职责"),
                ("coverage_match_summary", "coverage-match-summary.json", "覆盖数据来源与匹配诊断"),
            ]]


def scene_rubric_paths(run_dir: Path, spec: dict, role: str, selected: list[str], language: str | None = None) -> list[str]:
    root = run_dir / "inputs" / "methodologies"
    available = {p.stem: p for p in (root / "builtin").glob("*.md")}
    manifest = read_json(root / "manifest.json")
    user = {}
    for item in manifest.get("enabled_user_methodologies", []):
        path = Path(item["path"])
        path.resolve().relative_to(root.resolve())
        user[item["methodology_id"]] = path
    required = [*spec[f"{role}_rubrics"], *spec.get("language_rubrics", {}).get(role, {}).get(language, [])]
    missing = [name for name in required if name not in available or not available[name].is_file()]
    if missing:
        raise ValueError(f"冻结场景规则不可读取：{', '.join(missing)}")
    # Unknown optional IDs remain visible on the task for the worker to resolve;
    # they never give Python permission to reject a semantic result.
    paths = [available[name] for name in required]
    paths.extend(user.get(name, available.get(name)) for name in selected if name in user or name in available)
    return list(dict.fromkeys(str(path) for path in paths))
