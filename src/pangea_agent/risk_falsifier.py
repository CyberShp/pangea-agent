"""Bounded, worker-owned caller investigation; diagnostics never grade a risk."""
from __future__ import annotations
from pangea_agent.evidence_quotes import check_quotes

FALSIFIER_CONTRACT = "high-risk-caller-falsifier-v1"
_LEVELS = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def falsifier_diagnostics(body, resolver=None):
    if not isinstance(body, dict):
        return []
    severity = str(body.get("severity", "")).lower()
    trace = body.get("upstream_falsification")
    if severity not in {"high", "critical"} and trace is None:
        return []
    warnings = []
    def warn(field, code, message):
        warnings.append({"kind": "risk_falsifier_hint", "field": "body.upstream_falsification" + field,
                         "code": code, "message": message})
    if not isinstance(trace, dict):
        warn("", "caller_evidence_pending", "High/Critical 风险尚无一次窄域 Caller 反证记录；原 worker 在当前任务检查上游 1–2 层，缺证据保留原风险并标 UNRESOLVED。")
        return warnings
    if trace.get("format_version") != FALSIFIER_CONTRACT:
        warn(".format_version", "invalid_format", "反证记录格式应为 high-risk-caller-falsifier-v1。")
    initial = str(trace.get("initial_severity", "")).lower()
    if initial not in {"high", "critical"}:
        warn(".initial_severity", "missing_original_grade", "须保留反证前显式 High/Critical 等级，不能据标题猜测。")
    if type(trace.get("depth")) is not int or trace["depth"] not in (0, 1, 2):
        warn(".depth", "invalid_depth", "只记录实际追溯深度 0、1、2；0 表示未取得上游证据。")
    outcome = trace.get("outcome")
    if outcome not in {"counterevidence_found", "no_counterevidence", "unresolved"}:
        warn(".outcome", "invalid_outcome", "须显式记录 counterevidence_found/no_counterevidence/unresolved。")
    if not isinstance(trace.get("reason"), str) or not trace["reason"].strip():
        warn(".reason", "missing_reason", "说明拦截条件与触发条件关系，或尚缺的调用链证据；不自动推断。")
    if trace.get("depth") == 0 and outcome != "unresolved":
        warn(".outcome", "untraced_outcome", "未追溯 Caller 时结论待确认，不能报告已排除或已证明可达。")
    if severity in _LEVELS and initial in _LEVELS and _LEVELS[severity] > _LEVELS[initial]:
        warn(".initial_severity", "grade_increase", "本轮反证只做减法；当前等级高于原等级，由原 worker 核对，Python 不改写。")
    if severity != initial and outcome != "counterevidence_found":
        warn(".outcome", "unsupported_grade_change", "没有明确反证时应保留原等级；当前记录存在待核对的等级变化。")
    if outcome in {"counterevidence_found", "no_counterevidence"}:
        if resolver is None:
            warn(".source_evidence", "source_context_unavailable", "冻结 Caller 源码上下文不可用，反证未核实。")
        else:
            for error in check_quotes(trace.get("evidence_quotes"), resolver, required_refs=trace.get("source_evidence")):
                warn(".evidence_quotes", "caller_quote_mismatch", error)
    return warnings
