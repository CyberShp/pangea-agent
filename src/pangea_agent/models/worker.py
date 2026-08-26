from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pangea_agent.agent_io import agent_path

from .evidence import EvidenceRef
from .risk import RiskCard
from .testcase import TestCase


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisUnit(StrictModel):
    unit_id: str = Field(min_length=1)
    repo_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_scope: list[str] = Field(min_length=1)
    context_scope: list[str] = Field(default_factory=list)
    focus: list[str] = Field(min_length=1)
    dfx: list[str] = Field(min_length=1)
    languages: list[Literal["c_cpp", "lua"]] = Field(
        default_factory=lambda: ["c_cpp"],
        min_length=1,
    )
    frameworks: list[Literal["openubmc"]] = Field(default_factory=list)


class ReviewIssue(StrictModel):
    issue_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    required_change: str = Field(min_length=1)


class RepositoryRef(StrictModel):
    repo_id: str = Field(min_length=1)
    source_root: str = Field(min_length=1)
    git: dict = Field(default_factory=dict)

    @field_validator("source_root", mode="before")
    @classmethod
    def normalize_source_root(cls, value: str) -> str:
        return agent_path(value)


class CoverageGap(StrictModel):
    coverage_id: str = Field(min_length=1)
    gap: Literal["function_not_executed", "true_not_executed", "false_not_executed"]


class CoverageContext(StrictModel):
    repo_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    function: str = Field(min_length=1)
    count: int
    line: int | None = None
    module: str = ""
    coverage_type: Literal["function", "branch"] = "function"
    branch_id: str | None = None
    condition: str | None = None
    true_count: int | None = None
    false_count: int | None = None
    gaps: list[CoverageGap] = Field(default_factory=list)

    @model_validator(mode="after")
    def derive_gaps(self) -> "CoverageContext":
        if self.gaps:
            return self
        prefix = f"COV:{self.repo_id}:{self.path}:{self.function}"
        if self.coverage_type == "function":
            if self.count == 0:
                self.gaps = [CoverageGap(
                    coverage_id=f"{prefix}:function",
                    gap="function_not_executed",
                )]
            return self
        return self


class FailureSignalContext(StrictModel):
    path: str = Field(min_length=1)
    line: int = Field(gt=0)
    signal: str = Field(min_length=1)
    analysis_focus: str | None = Field(default=None, min_length=1)
    related_state_context: list[str] = Field(default_factory=list)


class SemanticCheckItem(StrictModel):
    check_id: str = Field(min_length=1)
    kind: Literal[
        "assertion_reachability",
        "resource_reconfiguration",
        "paired_operation",
        "runtime_semantics",
    ]
    subject_path: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    context_paths: list[str] = Field(min_length=1)


class BusinessFlow(StrictModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    mermaid: str | None = None
    evidence: list[EvidenceRef] = Field(min_length=1)


class VisualFinding(StrictModel):
    attachment_path: str = Field(min_length=1)
    observation: str = Field(min_length=1)
    status: Literal["confirmed", "pending_confirmation"] = "confirmed"
    pending_reason: str | None = None


class FailurePathCheck(StrictModel):
    path_id: str = Field(min_length=1)
    linked_risk_ids: list[str] = Field(default_factory=list)
    trigger: str = Field(min_length=1)
    side_effects: str = Field(min_length=1)
    failure: str = Field(min_length=1)
    caller_handling: str = Field(min_length=1)
    final_states: str = Field(min_length=1)
    disposition: Literal["risk", "excluded", "unresolved"]


class MaterialDecision(StrictModel):
    path: str = Field(min_length=1)
    decision: Literal["current", "context", "excluded"]
    reason: str = Field(min_length=1)


class CoverageDecision(StrictModel):
    coverage_id: str = Field(min_length=1)
    disposition: Literal[
        "covered_by_existing_case",
        "new_coverage_case",
        "unreachable_from_supported_entry",
    ]
    linked_test_case_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_links(self) -> "CoverageDecision":
        if self.disposition == "unreachable_from_supported_entry":
            if self.linked_test_case_ids:
                raise ValueError("不可从支持入口触达的 Coverage 缺口不能关联测试用例")
        return self


class AnalysisCheckpoint(StrictModel):
    source_paths_reviewed: list[str]
    lifecycle_stages_checked: list[str]
    failure_paths: list[FailurePathCheck] = Field(default_factory=list)
    material_decisions: list[MaterialDecision] = Field(default_factory=list)
    coverage_priorities: list[str] = Field(default_factory=list)
    coverage_decisions: list[CoverageDecision] = Field(default_factory=list)
    risk_set_frozen: bool = False
    counterexamples_checked: list[str] = Field(default_factory=list)


class WorkerTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["analysis", "rework"]
    stage: Literal[
        "source_checkpoint",
        "risk_analysis",
        "test_generation",
        "rework",
    ]
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    unit: AnalysisUnit
    repositories: list[RepositoryRef] = Field(min_length=1)
    index_path: str = Field(min_length=1)
    inventory_path: str | None
    source_manifest_path: str | None
    allowed_material_paths: list[str] = Field(default_factory=list)
    checkpoint_rubric_paths: list[str] = Field(
        default_factory=lambda: ["src/pangea_agent/rubrics/builtin/c_cpp_analysis.md"],
        min_length=1,
    )
    coverage_context: list[CoverageContext] = Field(default_factory=list)
    failure_signal_context: list[FailureSignalContext] = Field(default_factory=list)
    semantic_check_items: list[SemanticCheckItem] = Field(default_factory=list)
    attempt: Literal[0, 1]
    result_path: str = Field(min_length=1)
    max_parallel_workers: Literal[8] = 8
    may_spawn_workers: Literal[False] = False
    preferred_worker_id: str | None = None
    replacement_allowed: bool = False
    prior_result_path: str | None = None
    review_issues: list[ReviewIssue] = Field(default_factory=list)
    validation_feedback: list[str] = Field(default_factory=list)

    @field_validator(
        "index_path",
        "inventory_path",
        "source_manifest_path",
        "result_path",
        "prior_result_path",
        mode="before",
    )
    @classmethod
    def normalize_paths(cls, value: str | None) -> str | None:
        return None if value is None else agent_path(value)

    @model_validator(mode="after")
    def validate_task_type(self) -> "WorkerTask":
        if self.task_type == "analysis" and self.stage == "source_checkpoint" and (
            self.inventory_path
            or self.source_manifest_path
            or self.allowed_material_paths
            or self.coverage_context
        ):
            raise ValueError("source_checkpoint task 不能暴露后续阶段输入")
        if self.stage != "source_checkpoint" and (
            not self.inventory_path or not self.source_manifest_path
        ):
            raise ValueError("risk、test 和 rework task 必须包含冻结资料路径")
        if self.task_type == "analysis" and (
            self.stage == "rework"
            or self.attempt != 0
            or self.prior_result_path
            or self.review_issues
        ):
            raise ValueError("analysis task 只能是 attempt=0，且不能携带返工字段")
        if self.task_type == "rework" and (
            self.stage != "rework"
            or self.attempt != 1
            or not self.prior_result_path
            or not self.review_issues
        ):
            raise ValueError("rework task 必须绑定原结果和 review issues")
        return self


class WorkerResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    worker_id: str = Field(min_length=1)
    attempt: Literal[0, 1]
    completed_stage: Literal[
        "pending",
        "source_checkpoint",
        "risk_analysis",
        "test_generation",
        "rework",
    ]
    finish_reason: Literal["stop", "truncated", "error"]
    summary: str = Field(min_length=1)
    analyzed_scope: list[str] = Field(min_length=1)
    analyzed_context_scope: list[str]
    evidence: list[EvidenceRef]
    business_flows: list[BusinessFlow]
    visual_findings: list[VisualFinding]
    risks: list[RiskCard]
    test_cases: list[TestCase]
    addressed_review_issue_ids: list[str]
    errors: list[str]
    analysis_checkpoint: AnalysisCheckpoint

    @model_validator(mode="after")
    def validate_links(self) -> "WorkerResult":
        final_stage = self.completed_stage in {"test_generation", "rework"}
        if final_stage and self.finish_reason == "stop" and (
            not self.evidence or not self.business_flows
        ):
            raise ValueError("完整 worker 结果必须包含单元证据和业务流程")
        risk_ids = {risk.risk_id for risk in self.risks}
        for case in self.test_cases:
            unknown = set(case.linked_risk_ids) - risk_ids
            if unknown:
                raise ValueError(f"测试用例引用了当前单元不存在的风险：{sorted(unknown)}")
        if self.finish_reason == "stop" and self.errors:
            raise ValueError("finish_reason=stop 时 errors 必须为空")
        return self


class ResultRef(StrictModel):
    unit_id: str
    result_path: str

    @field_validator("result_path", mode="before")
    @classmethod
    def normalize_result_path(cls, value: str) -> str:
        return agent_path(value)


class TaskRef(StrictModel):
    unit_id: str
    task_path: str

    @field_validator("task_path", mode="before")
    @classmethod
    def normalize_task_path(cls, value: str) -> str:
        return agent_path(value)


class ReviewTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    repositories: list[RepositoryRef] = Field(min_length=1)
    inventory_path: str = Field(min_length=1)
    source_manifest_path: str = Field(min_length=1)
    stage: Literal[
        "independent_review",
        "comparison_review",
        "rework_verification",
    ]
    result_path: str = Field(min_length=1)
    analysis_tasks: list[TaskRef] = Field(default_factory=list)
    analysis_results: list[ResultRef] = Field(default_factory=list)
    independent_result_path: str | None = None
    may_spawn_workers: Literal[False] = False
    review_round: Literal[1] = 1
    same_reviewer_id: str | None = None
    prior_issues: list[ReviewIssue] = Field(default_factory=list)

    @field_validator(
        "inventory_path",
        "source_manifest_path",
        "result_path",
        "independent_result_path",
        mode="before",
    )
    @classmethod
    def normalize_paths(cls, value: str | None) -> str | None:
        return None if value is None else agent_path(value)

    @model_validator(mode="after")
    def validate_stage(self) -> "ReviewTask":
        if self.stage == "independent_review" and (
            not self.analysis_tasks
            or self.analysis_results
            or self.independent_result_path is not None
            or self.same_reviewer_id is not None
            or self.prior_issues
        ):
            raise ValueError("独立复核只能读取 analysis task，不能携带 worker result 或既有复核结论")
        if self.stage == "comparison_review" and (
            not self.analysis_tasks
            or not self.analysis_results
            or not self.independent_result_path
            or not self.same_reviewer_id
            or self.prior_issues
        ):
            raise ValueError("对照复核必须绑定独立复核结果、原 reviewer、analysis task 和 worker result")
        if self.stage == "rework_verification" and (
            not self.analysis_tasks
            or not self.analysis_results
            or not self.independent_result_path
            or not self.same_reviewer_id
            or not self.prior_issues
        ):
            raise ValueError("返工复核必须绑定独立复核结果、原 reviewer、初审问题和分析输入")
        return self


class IndependentReviewFinding(StrictModel):
    unit_id: str = Field(min_length=1)
    check_id: str = Field(min_length=1)
    finding: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)


class IndependentReviewResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    finish_reason: Literal["stop", "truncated", "error"]
    summary: str = Field(min_length=1)
    reviewed_units: list[str] = Field(min_length=1)
    findings: list[IndependentReviewFinding] = Field(default_factory=list)


class IndependentFinding(StrictModel):
    unit_id: str = Field(min_length=1)
    check_id: str | None = Field(default=None, min_length=1)
    finding: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    worker_disposition: Literal["covered", "reasonably_excluded", "missing", "contradiction"]
    linked_worker_risk_ids: list[str] = Field(default_factory=list)
    linked_worker_test_case_ids: list[str] = Field(default_factory=list)


class TestCaseCheck(StrictModel):
    unit_id: str = Field(min_length=1)
    test_case_id: str = Field(min_length=1)
    expected_results: list[str] = Field(min_length=1)
    failure_observations: list[str | None] = Field(min_length=1)
    current_behavior: str = Field(min_length=1)
    verdict: Literal["valid", "invalid", "unresolved"]
    reason: str = Field(min_length=1)


class ReviewResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    finish_reason: Literal["stop", "truncated", "error"]
    status: Literal["PASS", "REWORK", "UNRESOLVED"]
    summary: str = Field(min_length=1)
    issues: list[ReviewIssue] = Field(default_factory=list)
    reviewed_units: list[str] = Field(min_length=1)
    independent_findings: list[IndependentFinding] = Field(default_factory=list)
    test_case_checks: list[TestCaseCheck] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status(self) -> "ReviewResult":
        if self.status == "PASS" and self.issues:
            raise ValueError("PASS 不能包含待处理问题")
        blocking_findings = [
            finding.worker_disposition
            for finding in self.independent_findings
            if finding.worker_disposition in {"missing", "contradiction"}
        ]
        if self.status == "PASS" and blocking_findings:
            raise ValueError("PASS 不能包含 missing 或 contradiction 独立发现")
        if self.status in {"REWORK", "UNRESOLVED"} and not self.issues:
            raise ValueError(f"{self.status} 必须说明问题")
        if self.finish_reason != "stop" and self.status != "UNRESOLVED":
            raise ValueError("非完整复核结果只能标记 UNRESOLVED")
        return self


class ReviewerUnavailable(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    status: Literal["UNRESOLVED"] = "UNRESOLVED"


class TerminationSignal(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    phase: Literal[
        "WAITING_INDEPENDENT_REVIEW",
        "WAITING_COMPARISON_REVIEW",
        "WAITING_REWORK",
        "WAITING_REWORK_VERIFICATION",
    ]
    reason: str = Field(min_length=1)
    status: Literal["UNRESOLVED"] = "UNRESOLVED"
