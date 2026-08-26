from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepositoryRef(StrictModel):
    repo_id: str = Field(min_length=1)
    source_root: str = Field(min_length=1)
    git: dict = Field(default_factory=dict)


class SourceEvidence(StrictModel):
    repo_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    line_start: int = Field(gt=0)
    line_end: int | None = Field(default=None, gt=0)
    observation: str = Field(min_length=1)

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        return value.replace("\\", "/").strip("/")

    @model_validator(mode="after")
    def ordered_lines(self) -> "SourceEvidence":
        if self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("line_end 不能小于 line_start")
        return self


class ProposedUnit(StrictModel):
    repo_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_scope: list[str] = Field(min_length=1)
    context_scope: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    asset_item_ids: list[str] = Field(default_factory=list)
    coverage_ids: list[str] = Field(default_factory=list)
    mechanism_ids: list[str] = Field(default_factory=list)


class AnalysisUnit(ProposedUnit):
    unit_id: str = Field(min_length=1)
    line_count: int = Field(ge=0)
    function_count: int = Field(ge=0)


class PlanningTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["unit_planning"] = "unit_planning"
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    repositories: list[RepositoryRef] = Field(min_length=1)
    requested_scope: list[str] = Field(min_length=1)
    compact_metadata_path: str = Field(min_length=1)
    asset_candidates_path: str = Field(min_length=1)
    result_schema_path: str = Field(default="schemas/planning_result.schema.json", min_length=1)
    result_path: str = Field(min_length=1)
    max_unit_lines: int = Field(default=5000, gt=0)
    max_unit_functions: int = Field(default=140, gt=0)
    merge_direct_call_chain_max_lines: int = Field(default=800, gt=0)
    merge_direct_call_chain_max_functions: int = Field(default=30, gt=0)


class PlanningResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    summary: str = Field(min_length=1)
    units: list[ProposedUnit] = Field(min_length=1)
    unresolved: list[str] = Field(default_factory=list)


class FlowStep(StrictModel):
    step_key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    kind: Literal["entry", "main", "branch", "error", "propagation", "recovery", "exit"]
    evidence: list[SourceEvidence] = Field(min_length=1)


class FlowEdge(StrictModel):
    source_step_key: str = Field(
        min_length=1,
        description="必须引用同一 CodeFlow.steps 中已定义的 step_key",
    )
    target_step_key: str = Field(
        min_length=1,
        description="必须引用同一 CodeFlow.steps 中已定义的 step_key",
    )
    condition: str | None = Field(default=None, min_length=1)


class CodeFlow(StrictModel):
    flow_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    entry: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    steps: list[FlowStep] = Field(min_length=1)
    edges: list[FlowEdge] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_step_references(self) -> "CodeFlow":
        step_keys = [step.step_key for step in self.steps]
        if len(step_keys) != len(set(step_keys)):
            raise ValueError(f"流程 {self.flow_key} 的 step_key 不能重复")
        known = set(step_keys)
        for edge in self.edges:
            missing = {
                key
                for key in (edge.source_step_key, edge.target_step_key)
                if key not in known
            }
            if missing:
                raise ValueError(
                    f"流程 {self.flow_key} 的 edge 引用了未知 step_key：{sorted(missing)}"
                )
        return self


class InputDecision(StrictModel):
    item_id: str = Field(min_length=1)
    disposition: Literal[
        "confirmed",
        "missing_in_code",
        "extra_in_code",
        "mismatch",
        "irrelevant",
        "unresolved",
    ]
    conclusion: str = Field(min_length=1)
    evidence: list[SourceEvidence] = Field(default_factory=list)


class CoverageDecision(StrictModel):
    coverage_id: str = Field(min_length=1)
    disposition: Literal["test_generated", "covered_by_generated_case", "unreachable", "unresolved"]
    test_case_keys: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class MechanismDecision(StrictModel):
    mechanism_id: str = Field(min_length=1)
    disposition: Literal["equivalent_present", "blocked", "irrelevant", "unresolved"]
    current_causal_chain: list[str] = Field(default_factory=list)
    test_case_keys: list[str] = Field(default_factory=list)
    conclusion: str = Field(min_length=1)
    evidence: list[SourceEvidence] = Field(default_factory=list)


class RiskFinding(StrictModel):
    risk_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    dfx: list[str] = Field(min_length=1)
    severity: Literal["Low", "Medium", "High", "Critical"]
    confidence: Literal["low", "medium", "high"]
    trigger: str = Field(min_length=1)
    system_result: str = Field(min_length=1)
    external_observation: str = Field(min_length=1)
    exclusion_condition: str = Field(min_length=1)
    evidence: list[SourceEvidence] = Field(min_length=1)


class TestStep(StrictModel):
    action: str = Field(min_length=1)
    expected_result: str = Field(min_length=1)


class GeneratedTestCase(StrictModel):
    case_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    basis: list[Literal[
        "code_flow", "coverage", "requirement", "design", "defect_mechanism", "risk"
    ]] = Field(min_length=1)
    covered_flow_keys: list[str] = Field(min_length=1)
    linked_input_ids: list[str] = Field(default_factory=list)
    linked_risk_keys: list[str] = Field(default_factory=list)
    level: Literal["blackbox", "graybox"]
    preconditions: list[str] = Field(min_length=1)
    steps: list[TestStep] = Field(min_length=1)
    observability: list[str] = Field(min_length=1)
    cleanup: list[str] = Field(min_length=1)


class ReviewFindingDecision(StrictModel):
    finding_key: str = Field(min_length=1)
    disposition: Literal["incorporated", "dismissed", "unresolved"]
    conclusion: str = Field(min_length=1)
    evidence: list[SourceEvidence] = Field(default_factory=list)


class UnitSemanticResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    summary: str = Field(min_length=1)
    flows: list[CodeFlow] = Field(min_length=1)
    input_decisions: list[InputDecision] = Field(default_factory=list)
    coverage_decisions: list[CoverageDecision] = Field(default_factory=list)
    mechanism_decisions: list[MechanismDecision] = Field(default_factory=list)
    risks: list[RiskFinding] = Field(default_factory=list)
    test_cases: list[GeneratedTestCase] = Field(default_factory=list)
    review_finding_decisions: list[ReviewFindingDecision] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_keys(self) -> "UnitSemanticResult":
        groups = {
            "flow_key": [item.flow_key for item in self.flows],
            "risk_key": [item.risk_key for item in self.risks],
            "case_key": [item.case_key for item in self.test_cases],
        }
        for name, values in groups.items():
            if len(values) != len(set(values)):
                raise ValueError(f"{name} 不能重复")
        known_risks = set(groups["risk_key"])
        known_flows = set(groups["flow_key"])
        for case in self.test_cases:
            unknown_flows = set(case.covered_flow_keys) - known_flows
            if unknown_flows:
                raise ValueError(f"测试用例引用了未知 flow_key：{sorted(unknown_flows)}")
            unknown = set(case.linked_risk_keys) - known_risks
            if unknown:
                raise ValueError(f"测试用例引用了未知 risk_key：{sorted(unknown)}")
        known_cases = set(groups["case_key"])
        for decision in [*self.coverage_decisions, *self.mechanism_decisions]:
            unknown = set(decision.test_case_keys) - known_cases
            if unknown:
                raise ValueError(f"处理决定引用了未知 case_key：{sorted(unknown)}")
        return self


class AnalysisTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["analysis"] = "analysis"
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    unit: AnalysisUnit
    repository: RepositoryRef
    inventory_path: str = Field(min_length=1)
    source_manifest_path: str = Field(min_length=1)
    selected_inputs_path: str = Field(min_length=1)
    coverage_context: list[dict] = Field(default_factory=list)
    result_schema_path: str = Field(default="schemas/analysis_result.schema.json", min_length=1)
    result_path: str = Field(min_length=1)
    rubric_paths: list[str] = Field(min_length=1)


class ReviewFinding(StrictModel):
    finding_key: str = Field(min_length=1)
    category: Literal[
        "missed_flow",
        "document_delta",
        "coverage_gap",
        "defect_mechanism",
        "risk",
        "test_oracle",
        "incorrect_conclusion",
    ]
    affected_unit_ids: list[str] = Field(min_length=1)
    linked_input_ids: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    required_check: str = Field(min_length=1)
    evidence: list[SourceEvidence] = Field(min_length=1)


class IndependentReviewTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["independent_review"] = "independent_review"
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    repositories: list[RepositoryRef] = Field(min_length=1)
    unit_plan_path: str = Field(min_length=1)
    inventory_path: str = Field(min_length=1)
    source_manifest_path: str = Field(min_length=1)
    selected_inputs_path: str = Field(min_length=1)
    rubric_paths: list[str] = Field(min_length=1)
    result_schema_path: str = Field(default="schemas/independent_review_result.schema.json", min_length=1)
    result_skeleton_path: str = Field(
        default="schemas/independent_review_result.skeleton.json",
        min_length=1,
    )
    result_path: str = Field(min_length=1)


class IndependentReviewResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    summary: str = Field(min_length=1)
    findings: list[ReviewFinding] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class IndependentFindingDecision(StrictModel):
    finding_key: str = Field(min_length=1)
    disposition: Literal["confirmed", "dismissed", "unresolved"]
    conclusion: str = Field(min_length=1)
    evidence: list[SourceEvidence] = Field(min_length=1)


class ComparisonReviewResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    summary: str = Field(min_length=1)
    independent_finding_decisions: list[IndependentFindingDecision] = Field(
        default_factory=list
    )
    findings: list[ReviewFinding] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class ComparisonReviewTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["comparison_review"] = "comparison_review"
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    unit_plan_path: str = Field(min_length=1)
    analysis_task_paths: dict[str, str] = Field(min_length=1)
    analysis_result_paths: dict[str, str] = Field(min_length=1)
    independent_review_result_path: str = Field(min_length=1)
    selected_inputs_path: str = Field(min_length=1)
    rubric_paths: list[str] = Field(min_length=1)
    result_schema_path: str = Field(
        default="schemas/comparison_review_result.schema.json", min_length=1
    )
    result_skeleton_path: str = Field(
        default="schemas/comparison_review_result.skeleton.json",
        min_length=1,
    )
    result_path: str = Field(min_length=1)


class ClosureTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["closure"] = "closure"
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    unit: AnalysisUnit
    repository: RepositoryRef
    original_task_path: str = Field(min_length=1)
    original_result_path: str = Field(min_length=1)
    review_findings: list[ReviewFinding] = Field(min_length=1)
    result_schema_path: str = Field(default="schemas/analysis_result.schema.json", min_length=1)
    result_path: str = Field(min_length=1)
    rubric_paths: list[str] = Field(min_length=1)


class AgentAction(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    action_id: str = Field(min_length=1)
    action: Literal["dispatch_agent", "continue_agent"]
    role: Literal["asset_extraction", "planning", "analysis", "review", "closure"]
    stage: Literal[
        "structured_extraction",
        "unit_planning",
        "unit_analysis",
        "independent_review",
        "comparison_review",
        "targeted_closure",
    ]
    task_path: str = Field(min_length=1)
    task_id: str | None = None


class ActionState(AgentAction):
    status: Literal["pending", "dispatched", "settled", "accepted", "failed"] = "pending"
    error: str | None = None


class WorkflowProgress(StrictModel):
    schema_version: Literal["3.0"] = "3.0"
    run_id: str = Field(min_length=1)
    lifecycle_status: Literal["running", "complete", "stopped", "failed"] = "running"
    stage: Literal[
        "preparing",
        "planning",
        "analyzing",
        "reviewing",
        "closing",
        "reporting",
        "complete",
    ] = "preparing"
    quality_status: Literal["PASS", "UNRESOLVED"] | None = None
    analysis_units: list[AnalysisUnit] = Field(default_factory=list)
    completed_analysis_units: list[str] = Field(default_factory=list)
    completed_closure_units: list[str] = Field(default_factory=list)
    actions: dict[str, ActionState] = Field(default_factory=dict)
    errors: list[dict] = Field(default_factory=list)
    report_path: str | None = None
    html_report_path: str | None = None
