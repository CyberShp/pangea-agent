from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class AnalysisUnit(StrictModel):
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
    result_schema_path: str = Field(default="schemas/planning_result.schema.json", min_length=1)
    result_skeleton_path: str | None = Field(default=None, min_length=1)
    result_example_path: str = Field(default="schemas/planning_result.example.json", min_length=1)
    result_path: str = Field(min_length=1)
    rubric_paths: list[str] = Field(default_factory=lambda: ["src/pangea_agent/rubrics/builtin/c_cpp_unit_planning.md"], min_length=1)
    max_unit_lines: int = Field(default=5000, gt=0)
    max_unit_functions: int = Field(default=140, gt=0)
    merge_direct_call_chain_max_lines: int = Field(default=800, gt=0)
    merge_direct_call_chain_max_functions: int = Field(default=30, gt=0)

    @property
    def result_contract_version(self) -> Literal["2.0"]:
        return "2.0"


class PlanningResult(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    summary: str = Field(min_length=1)
    units: list[ProposedUnit] = Field(min_length=1)
    source_ownership: dict[str, str] = Field(min_length=1, description="键必须与 Planning task 骨架中预填的 repo_id:path 完全一致；值必须引用 units[].unit_key。每个键在 JSON 对象中只能出现一次。")
    unresolved: list[str] = Field(default_factory=list)


ProposedUnitV2 = ProposedUnit
PlanningResultV2 = PlanningResult


class FlowStep(StrictModel):
    step_key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    kind: Literal["entry", "main", "branch", "error", "propagation", "recovery", "exit"]
    evidence: list[SourceEvidence] = Field(min_length=1)


class FlowEdge(StrictModel):
    source_step_key: str = Field(min_length=1, description="必须引用同一 CodeFlow.steps 中已定义的 step_key")
    target_step_key: str = Field(min_length=1, description="必须引用同一 CodeFlow.steps 中已定义的 step_key")
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
    disposition: Literal["confirmed", "missing_in_code", "extra_in_code", "mismatch", "irrelevant", "unresolved"]
    conclusion: str = Field(min_length=1)
    evidence: list[SourceEvidence] = Field(default_factory=list)


class BranchDecision(StrictModel):
    branch_id: str = Field(min_length=1)
    flow_key: str = Field(min_length=1)
    disposition: Literal["scenario_mapped", "merged", "not_test_relevant", "developer_confirm", "unreachable"]
    scenario_keys: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class CoverageDecision(StrictModel):
    coverage_id: str = Field(min_length=1)
    disposition: Literal["scenario_mapped", "merged", "developer_confirm", "unreachable"]
    scenario_keys: list[str] = Field(default_factory=list)
    test_case_keys: list[str] = Field(
        default_factory=list,
        description=(
            "Workflow 从 test_cases[].direct_coverage_claims 的真实 coverage_id "
            "直接派生；不按 linked_input_ids 或共享 Scenario 扩大关联"
        ),
    )
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
    test_disposition: Literal["test_required", "developer_confirm", "unreachable_from_supported_entry"] = "test_required"
    unreachable_reason: str | None = Field(default=None, min_length=1)
    unreachable_evidence: list[SourceEvidence] = Field(default_factory=list)


class TestScenario(StrictModel):
    scenario_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    readiness: Literal["blackbox_ready", "graybox_ready", "developer_confirm"]
    business_entry: str | None = Field(default=None, min_length=1)
    preconditions: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    external_oracles: list[str] = Field(default_factory=list)
    recovery: list[str] = Field(default_factory=list)
    covered_flow_keys: list[str] = Field(default_factory=list)
    branch_ids: list[str] = Field(default_factory=list)
    coverage_ids: list[str] = Field(default_factory=list)
    linked_risk_keys: list[str] = Field(default_factory=list)
    linked_input_ids: list[str] = Field(default_factory=list)
    evidence: list[SourceEvidence] = Field(default_factory=list)


class TestStep(StrictModel):
    action: str = Field(min_length=1)
    expected_result: str = Field(min_length=1)


class CoverageTargetClaim(StrictModel):
    coverage_id: str = Field(min_length=1)
    target: Literal[
        "function_execution",
        "branch_true_outcome",
        "branch_false_outcome",
    ]


class GeneratedTestCase(StrictModel):
    case_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    basis: list[Literal["code_flow", "coverage", "requirement", "design", "defect_mechanism", "risk"]] = Field(min_length=1)
    scenario_keys: list[str] = Field(min_length=1)
    covered_flow_keys: list[str] = Field(min_length=1)
    linked_input_ids: list[str] = Field(
        default_factory=list,
        description=(
            "仅列此 TestCase 的实际步骤和断言直接覆盖的输入 ID；共享 Scenario 不自动继承其全部输入。"
            "其中 Coverage ID 集合必须与 direct_coverage_claims[].coverage_id 集合一致"
        ),
    )
    direct_coverage_claims: list[CoverageTargetClaim] = Field(
        default_factory=list,
        description=(
            "Agent 对本 TestCase 亲自命中的精确零覆盖目标所作的结构化声明；"
            "target 只表示 function 执行或 branch true/false outcome，不从步骤文本推断"
        ),
    )
    linked_risk_keys: list[str] = Field(default_factory=list)
    level: Literal["blackbox", "graybox"]
    preconditions: list[str] = Field(min_length=1)
    steps: list[TestStep] = Field(min_length=1)
    observability: list[str] = Field(min_length=1)
    cleanup: list[str] = Field(min_length=1)


class ComparisonAuditTarget(StrictModel):
    audit_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    object_type: str = Field(min_length=1)
    object_key: str = Field(min_length=1)
    check: str = Field(min_length=1)
    observed_fields: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Workflow 从 validated Analysis 和对应 Analysis task 原样冻结的待核对字段；"
            "仅用于让 Reviewer 直接看到当前值，不代表 Python 已作语义裁决"
        ),
    )
    acceptance_rule: str = Field(
        default="",
        description=(
            "Workflow 为当前 object/check 提供的就地核对规则；仅约束 Reviewer 如何裁决，"
            "不表示 Python 已判断 observed_fields 的语义正确性"
        ),
    )


class ComparisonAuditDecision(StrictModel):
    audit_id: str = Field(min_length=1)
    disposition: Literal["accepted", "finding"]
    finding_keys: list[str] = Field(default_factory=list)
    conclusion: str = Field(
        default="",
        description="本 audit 对象与 check 的逐项核对结论；新任务要求非空，旧 artifact 缺失时保持兼容",
    )

    @model_validator(mode="after")
    def validate_finding_links(self) -> ComparisonAuditDecision:
        if self.disposition == "accepted" and self.finding_keys:
            raise ValueError("accepted audit decision must not link findings")
        if self.disposition == "finding" and not self.finding_keys:
            raise ValueError("finding audit decision must link at least one finding")
        return self


class CorrectionTargetRef(StrictModel):
    unit_id: str = Field(min_length=1)
    collection: Literal[
        "result",
        "flows",
        "input_decisions",
        "branch_decisions",
        "coverage_decisions",
        "mechanism_decisions",
        "risks",
        "scenarios",
        "test_cases",
    ]
    object_key: str | None = Field(default=None, min_length=1)
    field_path: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_target_identity(self) -> CorrectionTargetRef:
        if self.collection == "result":
            if self.object_key is not None:
                raise ValueError("result correction target must not set object_key")
            if self.field_path not in {"/summary", "/unresolved"}:
                raise ValueError(
                    "result correction target field_path must be /summary or /unresolved"
                )
            return self

        if self.object_key is None:
            if self.field_path is not None:
                raise ValueError("new collection object target must not set field_path")
            return self

        if self.field_path is not None and not self.field_path.startswith("/"):
            raise ValueError("field_path must be a relative RFC 6901 JSON Pointer")
        return self


class ValueSnapshot(StrictModel):
    exists: bool
    value: Any


class AtomicCorrectionTarget(StrictModel):
    correction_id: str = Field(min_length=1)
    target: CorrectionTargetRef
    required_state: str = Field(min_length=1)


class ClosureCorrectionTarget(AtomicCorrectionTarget):
    finding_key: str = Field(min_length=1)
    before: ValueSnapshot


class ReviewFindingDecision(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "if": {
                "properties": {"disposition": {"const": "dismissed"}},
                "required": ["disposition"],
            },
            "then": {
                "properties": {"evidence": {"minItems": 1}},
                "required": ["evidence"],
            },
        },
    )
    finding_key: str = Field(min_length=1)
    correction_id: str | None = Field(default=None, min_length=1)
    resolved_object_key: str | None = Field(default=None, min_length=1)
    disposition: Literal["incorporated", "dismissed", "unresolved"]
    conclusion: str = Field(min_length=1)
    evidence: list[SourceEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_dismissal_evidence(self):
        if self.disposition == "dismissed" and not self.evidence:
            raise ValueError("dismissed Closure 裁决必须提供反证 evidence")
        return self


class UnitSemanticResult(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    summary: str = Field(min_length=1)
    flows: list[CodeFlow] = Field(min_length=1)
    input_decisions: list[InputDecision] = Field(default_factory=list)
    branch_decisions: list[BranchDecision] = Field(default_factory=list)
    coverage_decisions: list[CoverageDecision] = Field(default_factory=list)
    mechanism_decisions: list[MechanismDecision] = Field(default_factory=list)
    risks: list[RiskFinding] = Field(default_factory=list)
    scenarios: list[TestScenario] = Field(default_factory=list)
    test_cases: list[GeneratedTestCase] = Field(default_factory=list)
    review_finding_decisions: list[ReviewFindingDecision] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list, description="通常必须为空。仅记录真实 selected input ID、Coverage ID 或 confirmed finding_key 在冻结范围内无法完成规定裁决的阻断事项；范围外实现、外部文档、后续研究、低置信度和测试建议不得写入。")


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
    result_skeleton_path: str = Field(default="schemas/analysis_result.skeleton.json", min_length=1)
    result_example_path: str = Field(default="schemas/analysis_result.example.json", min_length=1)
    result_path: str = Field(min_length=1)
    rubric_paths: list[str] = Field(min_length=1)


class ReviewFinding(StrictModel):
    finding_key: str = Field(min_length=1)
    category: Literal["missed_flow", "document_delta", "coverage_gap", "defect_mechanism", "risk", "test_oracle", "incorrect_conclusion", "blackbox_translation"] = Field(description="新增 finding 的类别；blackbox_translation 表示源码事实/风险可能成立，但 Scenario/TestCase 翻译出的业务入口、测试动作、可达路径、Coverage/Risk 追溯或外部 Oracle 不受冻结证据支持；不得写入 independent_finding_decisions.disposition")
    affected_unit_ids: list[str] = Field(min_length=1)
    linked_input_ids: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    required_check: str = Field(min_length=1)
    evidence: list[SourceEvidence] = Field(min_length=1)
    correction_targets: list[AtomicCorrectionTarget] = Field(default_factory=list)


class IndependentReviewTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["independent_review"] = "independent_review"
    action_id: str | None = Field(default=None, min_length=1)
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    analysis_language: AnalysisLanguage = "c_cpp"
    repositories: list[RepositoryRef] = Field(min_length=1)
    evidence_scope_by_unit: dict[str, EvidenceScopeContract] = Field(default_factory=dict)
    unit_plan_path: str = Field(min_length=1)
    inventory_path: str = Field(min_length=1)
    source_manifest_path: str = Field(min_length=1)
    selected_inputs_path: str = Field(min_length=1)
    rubric_paths: list[str] = Field(min_length=1)
    result_schema_path: str = Field(default="schemas/independent_review_result.schema.json", min_length=1)
    result_skeleton_path: str = Field(default="schemas/independent_review_result.skeleton.json", min_length=1)
    result_path: str = Field(min_length=1)


class IndependentReviewResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    summary: str = Field(min_length=1)
    findings: list[ReviewFinding] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list, description="通常必须为空。仅当盲审任务的真实冻结输入本身缺失、无法完成盲审时填写；范围外实现、外部文档、研究问题和低置信度 finding 不得写入。")

    @model_validator(mode="after")
    def forbid_comparison_only_categories(self):
        invalid = [
            finding.finding_key
            for finding in self.findings
            if finding.category == "blackbox_translation"
        ]
        if invalid:
            raise ValueError(
                "independent_review 看不到 Analysis Result，不能使用 "
                f"blackbox_translation：{invalid}"
            )
        targeted = [
            finding.finding_key
            for finding in self.findings
            if finding.correction_targets
        ]
        if targeted:
            raise ValueError(
                "independent_review 看不到 Analysis Result，不能预填 "
                f"correction_targets：{targeted}"
            )
        return self


class IndependentFindingDecision(StrictModel):
    finding_key: str = Field(min_length=1, description="必须且只能引用 independent_review_result_path 顶层 findings[] 中已有的 finding_key；不得引用 Worker risk、flow、test case 或 Coverage 编号")
    disposition: Literal["confirmed", "dismissed", "unresolved"] = Field(description="只表示对盲审 finding 的裁决；不得填写 risk、incorrect_conclusion 等 category")
    conclusion: str = Field(min_length=1)
    evidence: list[SourceEvidence] = Field(
        default_factory=list,
        description="dismissed 必须提供用于核对 Analysis 等价覆盖或推翻 finding 的非空源码/契约证据；Analysis object/key/字段只写在 conclusion，不写进 SourceEvidence.observation",
    )
    correction_targets: list[AtomicCorrectionTarget] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_dismissal_evidence(self):
        if self.disposition == "dismissed" and not self.evidence:
            raise ValueError("dismissed 裁决必须提供非空核对 evidence；confirmed/unresolved 可复用原 finding evidence，不重复填写")
        return self


class ComparisonReviewResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    summary: str = Field(min_length=1)
    independent_finding_decisions: list[IndependentFindingDecision] = Field(default_factory=list, description="编号集合必须与 independent review 顶层 findings[].finding_key 完全相等；盲审 findings 为空时本列表也必须为空")
    findings: list[ReviewFinding] = Field(default_factory=list)
    analysis_audit_decisions: list[ComparisonAuditDecision] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list, description="通常必须为空。盲审 finding 无法裁决时只写对应 independent_finding_decisions 的 unresolved；范围外实现、外部文档、后续研究、低置信度和已交给 closure 的事项不得写入顶层。")


class ComparisonReviewTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["comparison_review"] = "comparison_review"
    action_id: str | None = Field(default=None, min_length=1)
    review_contract_version: Literal["1.0", "2.0"] = "1.0"
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    analysis_language: AnalysisLanguage = "c_cpp"
    evidence_scope_by_unit: dict[str, EvidenceScopeContract] = Field(default_factory=dict)
    unit_plan_path: str = Field(min_length=1)
    analysis_task_paths: dict[str, str] = Field(min_length=1)
    analysis_result_paths: dict[str, str] = Field(min_length=1)
    required_analysis_audits: list[ComparisonAuditTarget] = Field(default_factory=list)
    require_audit_conclusions: bool = False
    independent_review_result_path: str = Field(min_length=1)
    selected_inputs_path: str = Field(min_length=1)
    rubric_paths: list[str] = Field(min_length=1)
    result_schema_path: str = Field(default="schemas/comparison_review_result.schema.json", min_length=1)
    result_skeleton_path: str = Field(default="schemas/comparison_review_result.skeleton.json", min_length=1)
    result_path: str = Field(min_length=1)


class ClosureTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["closure"] = "closure"
    action_id: str | None = Field(default=None, min_length=1)
    review_contract_version: Literal["1.0", "2.0"] = "1.0"
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    analysis_language: AnalysisLanguage = "c_cpp"
    unit: AnalysisUnit
    evidence_scope: EvidenceScopeContract | None = None
    repository: RepositoryRef
    original_task_path: str = Field(min_length=1)
    original_result_path: str = Field(min_length=1)
    review_findings: list[ReviewFinding] = Field(default_factory=list)
    correction_targets: list[ClosureCorrectionTarget] = Field(default_factory=list)
    risk_test_obligations: list[str] = Field(default_factory=list)
    result_schema_path: str = Field(default="schemas/analysis_result.schema.json", min_length=1)
    result_example_path: str = Field(default="schemas/analysis_result.example.json", min_length=1)
    result_path: str = Field(min_length=1)
    rubric_paths: list[str] = Field(min_length=1)


class AgentAction(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    action_id: str = Field(min_length=1)
    action: Literal["dispatch_agent", "continue_agent"]
    role: Literal["asset_extraction", "planning", "analysis", "review", "closure"]
    stage: Literal["structured_extraction", "unit_planning", "unit_analysis", "independent_review", "comparison_review", "targeted_closure"]
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
    stage: Literal["preparing", "planning", "analyzing", "reviewing", "closing", "reporting", "complete"] = "preparing"
    quality_status: Literal["PASS", "UNRESOLVED"] | None = None
    analysis_units: list[AnalysisUnit] = Field(default_factory=list)
    completed_analysis_units: list[str] = Field(default_factory=list)
    completed_closure_units: list[str] = Field(default_factory=list)
    actions: dict[str, ActionState] = Field(default_factory=dict)
    degradations: list[dict] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
    report_path: str | None = None
    html_report_path: str | None = None
