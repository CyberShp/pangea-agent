"""End-to-end verification for risk-method-v2: DTS feedback, tolerant AST index, v2/v3 gates, and report rendering."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from pangea_agent.analysis_scenarios import scene_spec
from pangea_agent.dts_patterns import (
    freeze_dts_context,
    import_dts_patterns,
    ingest_dts_file,
    list_dts_patterns,
    set_dts_pattern_status,
)
from pangea_agent.graph.result_store import ResultStoreError, append_records, initialize_result, read_result
from pangea_agent.inventory.risk_topology_index import scan_file_topology
from pangea_agent.models.source_first import SourceBinding
from pangea_agent.report.risk_method import ledger_markdown
from pangea_agent.risk_v2_schema import (
    CHECK_FORMAT_V2,
    RISK_BLUEPRINT_FORMAT,
    RISK_FORMAT_V3,
    RISK_METHOD_V2,
    blueprint_coverage_diagnostics,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def verify_dts_feedback_and_git_diff(tmp: Path) -> None:
    data_root = tmp / "pangea-data"
    repo_dir = data_root / "repositories" / "demo-repo"
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_dir, check=True)
    src = repo_dir / "sess.c"
    src.write_text("void cleanup(void *p) {\n    // missing free\n}\n", encoding="utf-8")
    subprocess.run(["git", "add", "sess.c"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_dir, check=True)
    src.write_text("void cleanup(void *p) {\n    if (p) free(p);\n}\n", encoding="utf-8")
    subprocess.run(["git", "add", "sess.c"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fix leak"], cwd=repo_dir, check=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True).strip()

    csv_file = tmp / "dts_export.csv"
    csv_file.write_text(
        "dts_id,title,symptom,root_cause,fix,module,commit\n"
        f"DTS20260001,会话异常退出导致内存泄漏,长稳压测内存持续增长,错误分支未释放 auth_ctx 导致内存泄漏,补齐 free(p),src/sess.c,{sha}\n"
        "DTS20260002,登录界面按钮文案错别字,按钮文字错别字,界面文案拼写错误,修改提示文案,ui/login.js,\n",
        encoding="utf-8",
    )
    res = ingest_dts_file(data_root, csv_file)
    _assert(len(res["imported"]) == 1, f"expected 1 valid DTS card, got {res}")
    _assert(len(res["filtered_out"]) == 1, f"expected 1 noise row filtered out, got {res}")
    listed = list_dts_patterns(data_root)
    _assert(listed["total"] == 1, "registry should have 1 pattern")
    card = listed["items"][0]
    _assert(card["status"] == "enabled", "new DTS card must default to enabled")
    _assert(card["track"] == "resource_lifecycle", f"unexpected track: {card['track']}")
    _assert(card["has_diff_evidence"] is True, "git diff evidence should be attached")
    _assert("free(p)" in card["fixed_guard"], f"expected fixed_guard from git diff: {card['fixed_guard']}")

    # Disable and re-enable
    disabled = set_dts_pattern_status(data_root, card["pattern_id"], "disabled")
    _assert(disabled["status"] == "disabled", "disable failed")
    set_dts_pattern_status(data_root, card["pattern_id"], "enabled")

    # Freeze per-run context and verify immutability on re-call
    run_dir = data_root / "runs" / "run-v2-test"
    (run_dir / "inputs").mkdir(parents=True)
    snap1 = freeze_dts_context(data_root, run_dir, run_id="run-v2-test", scope_paths=["demo-repo:src/sess.c"])
    _assert(len(snap1["matched_patterns"]) == 1, "should match enabled DTS card")
    set_dts_pattern_status(data_root, card["pattern_id"], "disabled")
    snap2 = freeze_dts_context(data_root, run_dir, run_id="run-v2-test", scope_paths=["demo-repo:src/sess.c"])
    _assert(snap1["content_sha256"] == snap2["content_sha256"], "frozen dts-context must not mutate on resume")


def verify_tolerant_ast_topology() -> None:
    c_code = """
CUSTOM_WEIRD_MACRO_NO_SEMI(foo)
static int g_active_count = 0;

int sess_open(struct sess *s) {
    if (!s) return -1; // pure guard early return - should NOT pollute error_slices
    pthread_mutex_lock(&s->lock);
    s->buf = malloc(128);
    if (!s->buf) {
        goto err_unlock;
    }
    g_active_count++;
    if (recv(s->fd, s->buf, 128, 0) < 0) {
        return -2; // leak + lock held!
    }
    pthread_mutex_unlock(&s->lock);
    return 0;
err_unlock:
    pthread_mutex_unlock(&s->lock);
    return -1;
}

void sess_close(struct sess *s) {
    s->buf = NULL;
    g_active_count = 0;
}
"""
    c_res = scan_file_topology("demo", "src/sess.c", c_code, "c_cpp")
    _assert(c_res["parser"].startswith("tree-sitter"), f"should not fallback on macro error: {c_res['parser']}")
    err_lines = [s["line"] for s in c_res["error_slices"]]
    _assert(6 not in err_lines, f"pure guard early-return L6 should be excluded: {err_lines}")
    _assert(any(s["target_label"] == "err_unlock" and "pthread_mutex_unlock" in s["label_cleanups"] for s in c_res["error_slices"]),
            f"goto cleanup summary missing: {c_res['error_slices']}")
    _assert(any(a["kind"] == "lock_held_call" and a["symbol"] == "recv" for a in c_res["concurrency_anchors"]),
            f"lock_held_call recv missing: {c_res['concurrency_anchors']}")
    _assert(any(a["kind"] == "acquire" and a["symbol"] == "malloc" for a in c_res["resource_anchors"]),
            f"malloc resource anchor missing: {c_res['resource_anchors']}")
    _assert(any(m["symbol"] == "buf" for m in c_res["shared_access_matrix"]),
            f"shared_access_matrix missing buf: {c_res['shared_access_matrix']}")

    lua_code = """
local M = {}
function M:handle_req(req)
    local st = self.session_state
    local ok, resp = pcall(skynet.call, self.peer, "lua", "auth", req)
    if not ok then
        error("auth failed")
    end
    self.session_state = resp.next_state
end
return M
"""
    lua_res = scan_file_topology("demo", "lua/auth.lua", lua_code, "lua")
    _assert(lua_res["parser"].startswith("tree-sitter-lua"), f"expected tree-sitter-lua: {lua_res['parser']}")
    _assert(any(a["kind"] == "lua_yield_point" for a in lua_res["concurrency_anchors"]), "missing lua_yield_point")
    _assert(any(a["kind"] == "lua_cross_yield_state" and a["symbol"] == "self.session_state" for a in lua_res["concurrency_anchors"]),
            f"missing lua_cross_yield_state: {lua_res['concurrency_anchors']}")


def verify_v2_gates_and_ledger(tmp: Path) -> None:
    spec = scene_spec("risk-analysis")
    _assert(spec["method_version"] == RISK_METHOD_V2, f"expected {RISK_METHOD_V2}")
    contracts = spec["record_contracts"]
    _assert(contracts["risk"] == RISK_FORMAT_V3, "expected behavior-risk-v3")
    _assert(contracts["check"] == CHECK_FORMAT_V2, "expected behavior-check-v2")
    _assert(contracts["blueprint"] == RISK_BLUEPRINT_FORMAT, "expected risk-blueprint-v1")

    class _MockResolver:
        def lines(self, repo: str, path: str) -> list[str]:
            return [
                "int foo(struct s *x) {",
                "    free(x->ctx);",
                "    return 0;",
                "}",
            ]

    class _MockContext:
        resolver = _MockResolver()

    result_path = tmp / "track_result.json"
    binding = SourceBinding(data_root=str(tmp), run_id="run-1", action_id="run-1:analysis:U1", task_id="task-1")
    initialize_result(result_path, binding)

    # 1. Forged quote must be rejected deterministically
    forged_risk = {
        "kind": "risk",
        "body": {
            "format_version": RISK_FORMAT_V3,
            "risk_id": "R-001",
            "track": "resource_lifecycle",
            "verification_status": "static_proven",
            "severity": "High",
            "title": "释放后使用",
            "trigger": "并发关闭",
            "interleaving_or_path": ["L2 释放 x->ctx"],
            "system_result": "崩溃",
            "external_observation": "ASAN 报错",
            "exclusion_condition": "串行化",
            "source_evidence": ["repo:src/foo.c:1-4"],
            "evidence_quotes": [{"ref": "repo:src/foo.c:1-4", "snippet": "THIS_CODE_DOES_NOT_EXIST();"}],
        },
    }
    try:
        append_records(result_path, binding, expected_revision=0, records=[forged_risk], record_contracts=contracts, context=_MockContext())
        raise AssertionError("forged quote must fail write gate")
    except ResultStoreError as exc:
        _assert("不在 repo:src/foo.c:1-4 中" in str(exc), f"unexpected error: {exc}")

    # 2. needs_runtime_verification without instrumentation.points must be rejected
    missing_inst = {
        "kind": "risk",
        "body": {
            **forged_risk["body"],
            "verification_status": "needs_runtime_verification",
            "runtime_uncertainty": "取决于外部库排空语义",
            "evidence_quotes": [{"ref": "repo:src/foo.c:1-4", "snippet": "free(x->ctx);"}],
        },
    }
    try:
        append_records(result_path, binding, expected_revision=0, records=[missing_inst], record_contracts=contracts, context=_MockContext())
        raise AssertionError("missing instrumentation.points must fail write gate")
    except ResultStoreError as exc:
        _assert("instrumentation.points" in str(exc), f"unexpected error: {exc}")

    # 3. Valid check-v2 + needs_runtime_verification risk-v3 succeeds
    valid_batch = [
        {
            "kind": "check",
            "body": {
                "format_version": CHECK_FORMAT_V2,
                "check_id": "CHK-RES-001",
                "track": "resource_lifecycle",
                "entity_ref": "RES-001",
                "matched_dts_pattern_ids": ["DTS20260001"],
                "object": "x->ctx 释放后回调访问",
                "sites": ["repo:src/foo.c:1-4"],
                "hypothesis": "释放后并发回调解引用",
                "conclusion": "needs_runtime_verification",
                "reasoning": "需验证外部库是否同步排空回调",
                "evidence_quotes": [{"ref": "repo:src/foo.c:1-4", "snippet": "free(x->ctx);"}],
                "risk_ids": ["R-001"],
            },
        },
        {
            "kind": "risk",
            "body": {
                **missing_inst["body"],
                "entity_ref": "RES-001",
                "matched_dts_pattern_ids": ["DTS20260001"],
                "instrumentation": {
                    "points": [{
                        "id": "INJ-001",
                        "kind": "race_window_delay",
                        "repo_id": "repo",
                        "path": "src/foo.c",
                        "symbol": "foo",
                        "action": "在 free(x->ctx) 后注入 50ms 延迟",
                        "observe": "ASAN heap-use-after-free",
                    }],
                },
            },
        },
    ]
    write_res = append_records(result_path, binding, expected_revision=0, records=valid_batch, record_contracts=contracts, context=_MockContext())
    _assert(write_res["revision"] == 1, f"expected revision 1, got {write_res}")

    loaded = read_result(result_path)
    diag = blueprint_coverage_diagnostics(
        "resource_lifecycle",
        {"resource_entities": [{"id": "RES-001", "name": "ctx"}, {"id": "RES-002", "name": "fd"}]},
        loaded.records,
        {"homologous_dts": [{"dts_id": "DTS20260001", "track": "resource_lifecycle"}]},
    )
    _assert(diag["missing_blueprint_entities"] == ["RES-002"], f"unexpected diag: {diag}")
    _assert(diag["unaddressed_homologous_dts"] == [], f"unexpected dts diag: {diag}")
    _assert(diag["needs_runtime_verification_count"] == 1, f"unexpected count: {diag}")

    md_lines, _ = ledger_markdown([({"task": {"unit_id": "U1", "risk_track": "resource_lifecycle", "record_contracts": contracts}}, loaded)], {})
    md_text = "\n".join(md_lines)
    _assert("三轨风险检查账本（risk-method-v2）" in md_text, "v2 ledger header missing")
    _assert("待动态实锤" in md_text, "v2 two-tier column missing")


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        verify_dts_feedback_and_git_diff(tmp)
        verify_tolerant_ast_topology()
        verify_v2_gates_and_ledger(tmp)
    print("ALL risk-method-v2 VERIFICATIONS PASSED")


if __name__ == "__main__":
    main()
