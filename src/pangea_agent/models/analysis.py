from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AnalysisLanguage = Literal["c_cpp", "lua"]


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

class ProposedUnit(StrictModel):
    repo_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_scope: list[str] = Field(min_length=1)
    context_scope: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    asset_item_ids: list[str] = Field(default_factory=list)
    coverage_ids: list[str] = Field(default_factory=list)
    mechanism_ids: list[str] = Field(default_factory=list)
    methodology_ids: list[str] = Field(default_factory=list)
    methodology_selection_reasons: dict[str, str] = Field(default_factory=dict)


class ProposedUnitV2(StrictModel):
    unit_key: str = Field(min_length=1)
    repo_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    context_scope: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    asset_item_ids: list[str] = Field(default_factory=list)
    coverage_ids: list[str] = Field(default_factory=list)
    mechanism_ids: list[str] = Field(default_factory=list)
    methodology_ids: list[str] = Field(default_factory=list)
    methodology_selection_reasons: dict[str, str] = Field(default_factory=dict)


class AnalysisUnit(ProposedUnit):
    unit_id: str = Field(min_length=1)
    line_count: int = Field(ge=0)
    function_count: int = Field(ge=0)


class PlanningTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["unit_planning"] = "unit_planning"
    action_id: str | None = Field(default=None, min_length=1)
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    analysis_language: AnalysisLanguage = "c_cpp"
    repositories: list[RepositoryRef] = Field(min_length=1)
    requested_scope: list[str] = Field(min_length=1)
    compact_metadata_path: str = Field(min_length=1)
    asset_candidates_path: str = Field(min_length=1)
    methodology_paths: list[str] = Field(default_factory=list)
    methodology_catalog_path: str | None = Field(default=None, min_length=1)
    result_contract_version: Literal["1.0", "2.0"] = "1.0"
    result_schema_path: str = Field(default="schemas/planning_result.schema.json", min_length=1)
    result_skeleton_path: str | None = Field(default=None, min_length=1)
    result_example_path: str = Field(
        default="schemas/planning_result.example.json",
        min_length=1,
    )
    result_path: str = Field(min_length=1)
    rubric_paths: list[str] = Field(
        default_factory=lambda: [
            "src/pangea_agent/rubrics/builtin/c_cpp_unit_planning.md"
        ],
        min_length=1,
    )
    max_unit_lines: int = Field(default=5000, gt=0)
    max_unit_functions: int = Field(default=140, gt=0)
    merge_direct_call_chain_max_lines: int = Field(default=800, gt=0)
    merge_direct_call_chain_max_functions: int = Field(default=30, gt=0)


class PlanningResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    summary: str = Field(min_length=1)
    units: list[ProposedUnit] = Field(min_length=1)
    unresolved: list[str] = Field(
        default_factory=list,
        description=(
            "通常必须为空。只有请求源码无法唯一归属或真实输入无法分配，因而无法生成有效 unit plan 时才填写；"
            "范围外文件、context_scope 依赖、后续研究、设计动机和共享状态说明不得写入。"
            "声称请求文件元数据缺失前，必须同时核对 planning metadata 的 owned_source_paths 和 files[].path。"
        ),
    )


class PlanningResultV2(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    summary: str = Field(min_length=1)
    units: list[ProposedUnitV2] = Field(min_length=1)
    source_ownership: dict[str, str] = Field(
        min_length=1,
        description=(
            "键必须与 Planning task 骨架中预填的 repo_id:path 完全一致；"
            "值必须引用 units[].unit_key。每个键在 JSON 对象中只能出现一次。"
        ),
    )
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
    test_disposition: Literal[
        "test_required",
        "unreachable_from_supported_entry",
    ] = "test_required"
    unreachable_reason: str | None = Field(default=None, min_length=1)
    unreachable_evidence: list[SourceEvidence] = Field(default_factory=list)


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
    unresolved: list[str] = Field(
        default_factory=list,
        description=(
            "通常必须为空。仅记录真实 selected input ID、Coverage ID 或 confirmed finding_key "
            "在冻结范围内无法完成规定裁决的阻断事项；范围外实现、外部文档、后续研究、低置信度和测试建议不得写入。"
        ),
    )


class EvidenceScopeContract(StrictModel):
    repo_id: str = Field(min_length=1)
    allowed_paths: list[str] = Field(min_length=1)


class AnalysisTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["analysis"] = "analysis"
    action_id: str | None = Field(default=None, min_length=1)
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    analysis_language: AnalysisLanguage = "c_cpp"
    unit: AnalysisUnit
    evidence_scope: EvidenceScopeContract | None = None
    repository: RepositoryRef
    inventory_path: str = Field(min_length=1)
    source_manifest_path: str = Field(min_length=1)
    selected_inputs_path: str = Field(min_length=1)
    coverage_context: list[dict] = Field(default_factory=list)
    result_schema_path: str = Field(default="schemas/analysis_result.schema.json", min_length=1)
    result_skeleton_path: str = Field(
        default="schemas/analysis_result.skeleton.json",
        min_length=1,
    )
    result_example_path: str = Field(
        default="schemas/analysis_result.example.json",
        min_length=1,
    )
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
    ] = Field(
        description=(
            "新增 finding 的类别；不得写入 independent_finding_decisions.disposition"
        )
    )
    affected_unit_ids: list[str] = Field(min_length=1)
    linked_input_ids: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    required_check: str = Field(min_length=1)
    evidence: list[SourceEvidence] = Field(min_length=1)


class IndependentReviewTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["independent_review"] = "independent_review"
    action_id: str | None = Field(default=None, min_length=1)
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    analysis_language: AnalysisLanguage = "c_cpp"
    repositories: list[RepositoryRef] = Field(min_length=1)
    evidence_scope_by_unit: dict[str, EvidenceScopeContract] = Field(
        default_factory=dict
    )
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
    unresolved: list[str] = Field(
        default_factory=list,
        description=(
            "通常必须为空。仅当盲审任务的真实冻结输入本身缺失、无法完成盲审时填写；"
            "范围外实现、外部文档、研究问题和低置信度 finding 不得写入。"
        ),
    )


class IndependentFindingDecision(StrictModel):
    finding_key: str = Field(
        min_length=1,
        description=(
            "必须且只能引用 independent_review_result_path 顶层 findings[] 中已有的 finding_key；"
            "不得引用 Worker risk、flow、test case 或 Coverage 编号"
        ),
    )
    disposition: Literal["confirmed", "dismissed", "unresolved"] = Field(
        description=(
            "只表示对盲审 finding 的裁决；不得填写 risk、incorrect_conclusion 等 category"
        )
    )
    conclusion: str = Field(min_length=1)
    evidence: list[SourceEvidence] = Field(min_length=1)


class ComparisonReviewResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    summary: str = Field(min_length=1)
    independent_finding_decisions: list[IndependentFindingDecision] = Field(
        default_factory=list,
        description=(
            "编号集合必须与 independent review 顶层 findings[].finding_key 完全相等；"
            "盲审 findings 为空时本列表也必须为空"
        ),
    )
    findings: list[ReviewFinding] = Field(default_factory=list)
    unresolved: list[str] = Field(
        default_factory=list,
        description=(
            "通常必须为空。盲审 finding 无法裁决时只写对应 independent_finding_decisions 的 unresolved；"
            "范围外实现、外部文档、后续研究、低置信度和已交给 closure 的事项不得写入顶层。"
        ),
    )


class ComparisonReviewTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["comparison_review"] = "comparison_review"
    action_id: str | None = Field(default=None, min_length=1)
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    analysis_language: AnalysisLanguage = "c_cpp"
    evidence_scope_by_unit: dict[str, EvidenceScopeContract] = Field(
        default_factory=dict
    )
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
    action_id: str | None = Field(default=None, min_length=1)
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    analysis_language: AnalysisLanguage = "c_cpp"
    unit: AnalysisUnit
    evidence_scope: EvidenceScopeContract | None = None
    repository: RepositoryRef
    original_task_path: str = Field(min_length=1)
    original_result_path: str = Field(min_length=1)
    review_findings: list[ReviewFinding] = Field(default_factory=list)
    risk_test_obligations: list[str] = Field(default_factory=list)
    result_schema_path: str = Field(default="schemas/analysis_result.schema.json", min_length=1)
    result_example_path: str = Field(
        default="schemas/analysis_result.example.json",
        min_length=1,
    )
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


class ValidationFailureRecord(StrictModel):
    attempt: int = Field(gt=0)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    detail_count: int = Field(default=0, ge=0)
    details: list[dict] = Field(default_factory=list)
    details_truncated: bool = False


class ActionState(AgentAction):
    status: Literal["pending", "dispatched", "settled", "accepted", "failed"] = "pending"
    error: str | None = None
    validation_failures: int = Field(default=0, ge=0)
    repeated_validation_failures: int = Field(default=0, ge=0)
    validation_history: list[ValidationFailureRecord] = Field(default_factory=list)
    incomplete_attempts: int = Field(default=0, ge=0)
    incomplete_history: list[ValidationFailureRecord] = Field(default_factory=list)


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
    degradations: list[dict] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
    report_path: str | None = None
    html_report_path: str | None = None
