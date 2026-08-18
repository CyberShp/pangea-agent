from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class ReviewIssue(StrictModel):
    issue_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    required_change: str = Field(min_length=1)


class RepositoryRef(StrictModel):
    repo_id: str = Field(min_length=1)
    source_root: str = Field(min_length=1)
    git: dict = Field(default_factory=dict)


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


class FailureSignalContext(StrictModel):
    path: str = Field(min_length=1)
    line: int = Field(gt=0)
    signal: str = Field(min_length=1)
    analysis_focus: str | None = Field(default=None, min_length=1)
    related_state_context: list[str] = Field(default_factory=list)


class SemanticCheckItem(StrictModel):
    check_id: str = Field(min_length=1)
    kind: Literal["assertion_reachability", "resource_reconfiguration", "paired_operation"]
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


class AnalysisCheckpoint(StrictModel):
    source_paths_reviewed: list[str] = Field(min_length=1)
    lifecycle_stages_checked: list[str] = Field(min_length=1)
    failure_paths: list[FailurePathCheck] = Field(default_factory=list)
    material_decisions: list[MaterialDecision] = Field(default_factory=list)
    coverage_priorities: list[str] = Field(default_factory=list)
    risk_set_frozen: bool = False
    counterexamples_checked: list[str] = Field(default_factory=list)


class WorkerTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["analysis", "rework"]
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    unit: AnalysisUnit
    repositories: list[RepositoryRef] = Field(min_length=1)
    index_path: str = Field(min_length=1)
    inventory_path: str = Field(min_length=1)
    source_manifest_path: str = Field(min_length=1)
    coverage_context: list[CoverageContext] = Field(default_factory=list)
    failure_signal_context: list[FailureSignalContext] = Field(default_factory=list)
    semantic_check_items: list[SemanticCheckItem] = Field(default_factory=list)
    attempt: Literal[0, 1]
    result_path: str = Field(min_length=1)
    max_parallel_workers: Literal[4] = 4
    may_spawn_workers: Literal[False] = False
    preferred_worker_id: str | None = None
    replacement_allowed: bool = False
    prior_result_path: str | None = None
    review_issues: list[ReviewIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_task_type(self) -> "WorkerTask":
        if self.task_type == "analysis" and (self.attempt != 0 or self.prior_result_path or self.review_issues):
            raise ValueError("analysis task 只能是 attempt=0，且不能携带返工字段")
        if self.task_type == "rework" and (
            self.attempt != 1 or not self.prior_result_path or not self.review_issues
        ):
            raise ValueError("rework task 必须绑定原结果和 review issues")
        return self


class WorkerResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    worker_id: str = Field(min_length=1)
    attempt: Literal[0, 1]
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
        if self.finish_reason == "stop" and (not self.evidence or not self.business_flows):
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


class ReviewTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    repositories: list[RepositoryRef] = Field(min_length=1)
    inventory_path: str = Field(min_length=1)
    source_manifest_path: str = Field(min_length=1)
    stage: Literal["initial_review", "rework_verification"] = "initial_review"
    result_path: str = Field(min_length=1)
    analysis_results: list[ResultRef] = Field(min_length=1)
    may_spawn_workers: Literal[False] = False
    review_round: Literal[1] = 1
    same_reviewer_id: str | None = None
    prior_issues: list[ReviewIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_stage(self) -> "ReviewTask":
        if self.stage == "initial_review" and (self.same_reviewer_id is not None or self.prior_issues):
            raise ValueError("初审任务不能预设 reviewer 或 prior issues")
        if self.stage == "rework_verification" and (not self.same_reviewer_id or not self.prior_issues):
            raise ValueError("返工复核必须绑定原 reviewer 和初审问题")
        return self


class IndependentFinding(StrictModel):
    unit_id: str = Field(min_length=1)
    finding: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    worker_disposition: Literal["covered", "reasonably_excluded", "missing", "contradiction"]


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
    phase: Literal["WAITING_REVIEW", "WAITING_REWORK", "WAITING_REWORK_REVIEW"]
    reason: str = Field(min_length=1)
    status: Literal["UNRESOLVED"] = "UNRESOLVED"
