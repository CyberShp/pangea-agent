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
    _assert("四轨风险检查账本（risk-method-v2）" in md_text, "v2 4-track ledger header missing")
    _assert("待动态实锤" in md_text, "v2 two-tier column missing")


def verify_memory_corruption_dts_and_topology(tmp: Path) -> None:
    data_root = tmp / "pangea-mem-data"
    repo_dir = data_root / "repositories" / "mem-repo"
    (repo_dir / "include").mkdir(parents=True)
    (repo_dir / "src").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_dir, check=True)

    # Create a header with >15 lines changed ahead of .c implementation change
    hdr = repo_dir / "include" / "slot_mgr.h"
    src_a = repo_dir / "src" / "chan_a.c"
    src_b = repo_dir / "src" / "chan_b.c"
    hdr.write_text("\n".join(f"#define HDR_CONST_{i} {i}" for i in range(20)) + "\n", encoding="utf-8")
    src_a.write_text(
        "void chan_a_teardown(struct chan *c) {\n"
        "    pool_free(c->slot);\n"
        "}\n"
        "void chan_a_async_cb(struct chan *c, const void *buf, size_t len) {\n"
        "    memcpy(c->slot->payload, buf, len);\n"
        "}\n",
        encoding="utf-8",
    )
    # Sibling latent 6.1.2 bug in chan_b.c (unfixed in 6.1.7)
    src_b.write_text(
        "void chan_b_teardown(struct chan *c) {\n"
        "    pool_free(c->slot);\n"
        "}\n"
        "void chan_b_async_cb(struct chan *c, const void *buf, size_t len) {\n"
        "    memcpy(c->slot->payload, buf, len);\n"
        "    c->slot->state = 2;\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "v6.1.2 baseline"], cwd=repo_dir, check=True)

    # Simulate 6.1.6 fix in chan_a.c + noisy header change
    hdr.write_text("\n".join(f"#define HDR_CONST_{i} {i + 100}" for i in range(20)) + "\n", encoding="utf-8")
    src_a.write_text(
        "void chan_a_teardown(struct chan *c) {\n"
        "    cancel_async_cb(c);\n"
        "    pool_free(c->slot);\n"
        "    c->slot = NULL;\n"
        "}\n"
        "void chan_a_async_cb(struct chan *c, const void *buf, size_t len) {\n"
        "    if (!c->slot || c->closing) return;\n"
        "    memcpy(c->slot->payload, buf, len);\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "DTS20260616: fix cross-thread slot stomp in chan_a"], cwd=repo_dir, check=True)
    fix_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True).strip()

    # 1. Verify .patch offline ingestion + memory_corruption classification
    patch_text = subprocess.check_output(["git", "show", fix_sha], cwd=repo_dir, text=True)
    patch_file = tmp / "DTS20260616.patch"
    patch_file.write_text(patch_text, encoding="utf-8")
    patch_res = ingest_dts_file(data_root, patch_file)
    _assert(len(patch_res["imported"]) == 1, f"expected 1 imported patch card, got {patch_res}")
    patch_card = list_dts_patterns(data_root)["items"][0]
    _assert(patch_card["track"] == "memory_corruption", f"expected memory_corruption track, got {patch_card}")
    _assert("cancel_async_cb" in patch_card["fixed_guard"], f".c hunk must not be truncated by .h lines: {patch_card['fixed_guard']}")

    # 2. Verify AST memory_anchors on sibling chan_b.c
    topo = scan_file_topology("mem-repo", "src/chan_b.c", src_b.read_text(encoding="utf-8"), "c_cpp")
    mem_kinds = {a["kind"] for a in topo.get("memory_anchors", [])}
    _assert("unnullified_member_after_release" in mem_kinds, f"missing unnullified_member_after_release: {topo['memory_anchors']}")
    _assert("cross_fn_dangling_candidate" in mem_kinds, f"missing cross_fn_dangling_candidate: {topo['memory_anchors']}")
    _assert("raw_memory_op" in mem_kinds, f"missing raw_memory_op: {topo['memory_anchors']}")

    # 3. Verify full Stage 1 -> Stage 2 4-track Graph flow + historical_defect asset + MR commit URL
    from pangea_agent.agent_io import read_json, write_json
    from pangea_agent.assets import (
        asset_detail,
        complete_asset_extraction,
        import_asset,
        prepare_asset_extraction,
        review_asset,
    )
    from pangea_agent.cli.adapter_api import bind_action, settle_action
    from pangea_agent.cli.plan_batch import plan_write_batch
    from pangea_agent.cli.run_module_analysis import run_module_analysis
    from pangea_agent.cli.source_first_api import result_write, task_open, work_finish

    defect_doc = tmp / "defect_616.md"
    defect_doc.write_text("# 历史踩内存缺陷\n跨线程访问已释放 slot 导致内存池重分配被踩\n", encoding="utf-8")
    asset = import_asset(str(data_root), str(defect_doc), "historical_defect")
    prep = prepare_asset_extraction(str(data_root), asset.asset_id)
    write_json(
        Path(prep["asset"]["result_path"]),
        {
            "schema_version": "1.0",
            "asset_id": asset.asset_id,
            "summary": "6.1.6 通道销毁跨线程释放后重分配踩内存案例",
            "items": [
                {
                    "item_type": "historical_defect",
                    "item_id": "HD-616",
                    "title": "6.1.6 通道销毁未排空异步回调导致踩内存",
                    "symptom": "并发断连压测出现不固定栈 coredump（邻居对象 magic 被覆写）",
                    "trigger": "销毁线程释放 slot 后，工作线程晚到回调继续写入 c->slot->payload",
                    "root_cause": "teardown 仅调用 pool_free(c->slot) 未排空回调且未置空指针",
                    "propagation": ["pool_free 归还槽位", "新会话复用该槽位", "旧回调 memcpy 踩坏新会话内存"],
                    "defect_mechanism": "跨线程释放后悬空指针写踩坏重分配内存池槽位",
                    "exclusion_conditions": ["销毁前同步排空异步队列并置空引用"],
                    "source_references": [{"path": "src/chan_a.c", "location": "chan_a_teardown"}],
                }
            ],
            "warnings": [],
        },
    )
    complete_asset_extraction(str(data_root), asset.asset_id)
    review_asset(str(data_root), asset.asset_id, "approve")
    asset_info = asset_detail(str(data_root), asset.asset_id)["asset"]
    candidate_id = f"{asset.asset_id}:HD-616"

    # Disable registry card so we specifically prove MR commit URL + historical_defect asset populate homologous_dts
    set_dts_pattern_status(data_root, patch_card["pattern_id"], "disabled")

    run_id = "run-4track-e2e"
    req_path = tmp / "request_4track.json"
    write_json(
        req_path,
        {
            "data_root": str(data_root),
            "run_id": run_id,
            "repository": "mem-repo",
            "target": "跨版本同源踩内存与资源生命周期排查",
            "workflow_version": "source-first-v1",
            "analysis_profile": "behavior-test-v2",
            "analysis_settings": {
                "scenario": "risk-analysis",
                "mode": "depth",
                "dts_context": {
                    "dts_ids": ["DTS20260616"],
                    "mr_urls": [f"https://codehub.example/org/mem-repo/-/merge_requests/88/commits/{fix_sha}"],
                },
            },
            "source_scope": ["src"],
            "asset_ids": [asset.asset_id],
            "asset_revisions": {asset.asset_id: asset_info["input_revision"]},
        },
    )

    started = run_module_analysis(str(req_path))
    plan_action = started["agent_actions"][0]
    p_bind = (str(data_root), run_id, plan_action["action_id"], "planner-4track")
    bind_action(*p_bind)
    opened_plan = task_open(*p_bind)
    plan_startup_ids = {
        p["input_id"]
        for bucket in ("pages", "pending_inputs")
        for p in opened_plan["planning_startup"][bucket]
    }
    _assert("scene_planning_rubric" in plan_startup_ids, f"missing scene_planning_rubric: {plan_startup_ids}")
    _assert("scene_planning_rubric_scene_risk_blueprint" in plan_startup_ids, f"missing scene_risk_blueprint: {plan_startup_ids}")
    _assert("dts_context" in plan_startup_ids, f"planning_startup must include dts_context: {plan_startup_ids}")
    _assert("asset_candidates" in plan_startup_ids, f"planning_startup must include asset_candidates: {plan_startup_ids}")

    # Stage 1 writes risk-blueprint-v1 (with memory_hazard_zones) + global note (relates_to=[])
    blueprint_records = [
        {
            "kind": "note",
            "body": {
                "format_version": RISK_BLUEPRINT_FORMAT,
                "resource_entities": [
                    {
                        "id": "RES-SLOT",
                        "name": "chan->slot",
                        "kind": "pool_slot",
                        "acquire_sites": ["mem-repo:src/chan_b.c:1-3"],
                        "release_sites": ["mem-repo:src/chan_b.c:1-3"],
                        "owner": "struct chan",
                    }
                ],
                "concurrency_contexts": [
                    {
                        "id": "CTX-TEARDOWN",
                        "name": "销毁线程 vs 异步回调线程",
                        "shared_state": ["c->slot"],
                        "expected_guard": "cancel_async_cb 排空屏障",
                    }
                ],
                "transaction_chains": [],
                "memory_hazard_zones": [
                    {
                        "id": "MEM-SLOT-01",
                        "name": "c->slot->payload 跨线程释放后重分配悬空写",
                        "release_or_reuse_sites": ["mem-repo:src/chan_b.c:2"],
                        "concurrent_access_sites": ["mem-repo:src/chan_b.c:5-6"],
                    }
                ],
            },
        },
        {
            "kind": "note",
            "relates_to": [],
            "body": {
                "mcp_dts_extraction": {
                    "dts_id": "DTS20260616",
                    "fixed_site": "mem-repo:src/chan_a.c:1-9 (已合入 cancel_async_cb + c->slot = NULL)",
                    "sibling_suspect": "mem-repo:src/chan_b.c:1-7 仍缺少排空与置空保护",
                },
            },
        },
    ]
    r_write = result_write(*p_bind, expected_revision=0, records=blueprint_records)

    shared_files = [
        {"repo_id": "mem-repo", "path": "src/chan_a.c"},
        {"repo_id": "mem-repo", "path": "src/chan_b.c"},
    ]

    # First verify same-track duplicate owned_files is STILL caught
    dup_plan = plan_write_batch(
        *p_bind,
        expected_revision=r_write["revision"],
        units=[
            {
                "title": "重复轨1",
                "purpose": "测试同轨冲突",
                "risk_track": "memory_corruption",
                "owned_files": shared_files,
            },
            {
                "title": "重复轨2",
                "purpose": "测试同轨冲突",
                "risk_track": "memory_corruption",
                "owned_files": shared_files,
            },
        ],
    )
    _assert(
        bool(dup_plan["diagnostics"]["duplicate_owned_regions"]),
        f"same-track duplicate owned_files must still trigger duplicate_owned_regions: {dup_plan}",
    )

    # Now update unit-0001/unit-0002 to distinct tracks and append tracks 3 & 4 sharing exact same owned_files
    batch_res = plan_write_batch(
        *p_bind,
        expected_revision=dup_plan["revision"],
        units=[
            {
                "unit_id": "unit-0001",
                "title": "轨1-错误回滚",
                "purpose": "排查错误分支漏清理",
                "risk_track": "error_rollback",
                "asset_item_ids": [candidate_id],
                "owned_files": shared_files,
            },
            {
                "unit_id": "unit-0002",
                "title": "轨2-并发时序",
                "purpose": "排查锁序与状态竞争",
                "risk_track": "concurrency_timing",
                "asset_item_ids": [candidate_id],
                "owned_files": shared_files,
            },
            {
                "title": "轨3-资源生命周期",
                "purpose": "排查泄漏与双重释放",
                "risk_track": "resource_lifecycle",
                "asset_item_ids": [candidate_id],
                "owned_files": shared_files,
            },
            {
                "title": "轨4-踩内存与悬空访问",
                "purpose": "排查跨线程释放后重分配踩内存与越界写",
                "risk_track": "memory_corruption",
                "asset_item_ids": [candidate_id],
                "owned_files": shared_files,
            },
        ],
    )
    _assert(batch_res["diagnostics"]["duplicate_owned_regions"] == [], f"distinct risk_track units must share owned_files cleanly: {batch_res['diagnostics']}")
    _assert(batch_res["diagnostics"]["ready"] is True, f"4-track plan should be ready: {batch_res['diagnostics']}")

    work_finish(*p_bind, revision=batch_res["revision"])
    stage2 = settle_action(*p_bind[:3])
    _assert(len(stage2["agent_actions"]) == 4, f"expected 4 Stage 2 track actions, got {len(stage2['agent_actions'])}")

    run_dir = data_root / "runs" / run_id
    _assert((run_dir / "inputs" / "risk-blueprint.json").is_file(), "risk-blueprint.json must be materialized for Stage 2")
    frozen_dts = read_json(run_dir / "inputs" / "dts-context.json")
    homologous = frozen_dts.get("homologous_dts", [])
    _assert(any(h.get("source_kind") == "historical_defect_asset" and h.get("track") == "memory_corruption" for h in homologous), f"historical_defect must project into homologous_dts: {homologous}")
    _assert(any(fix_sha in str(h.get("commit_ref", "")) and "cancel_async_cb" in str(h.get("already_fixed_check", "")) for h in homologous), f"MR commit SHA diff must be extracted into homologous_dts: {homologous}")

    tracks_seen: set[str] = set()
    flow_fixture = {
        "format_version": "behavior-flow-v1",
        "flow_id": "F-CHAN",
        "title": "通道销毁与异步回调时序",
        "role": "main",
        "attach_to": None,
        "text": "销毁路径释放 slot 与异步回调写入 payload 的交互。",
        "nodes": [
            {"id": "n1", "kind": "entry", "label": "chan_b_teardown"},
            {"id": "n2", "kind": "exit", "label": "pool_free(c->slot)"},
        ],
        "edges": [{"source_step_key": "n1", "target_step_key": "n2", "condition": ""}],
        "paths": [{"path_id": "p1", "node_ids": ["n1", "n2"]}],
        "source_evidence": ["mem-repo:src/chan_b.c:1-3"],
        "sequence": {
            "format_version": "sequence-v1",
            "title": "销毁与回调交错",
            "status": "ready",
            "participants": [{"id": "t1", "label": "销毁线程"}, {"id": "t2", "label": "回调线程"}],
            "messages": [{"id": "m1", "from": "t1", "to": "t2", "label": "未同步释放 slot", "kind": "async", "source_evidence": ["mem-repo:src/chan_b.c:2"]}],
            "fragments": [],
            "notes": [],
        },
        "call_graph": {
            "format_version": "call-graph-v1",
            "title": "chan_b 调用关系",
            "status": "ready",
            "root": "chan_b_teardown",
            "functions": [{"id": "chan_b_teardown", "kind": "function", "label": "chan_b_teardown", "signature": "void chan_b_teardown(struct chan *c)", "file": "src/chan_b.c", "line": 1}],
            "calls": [],
            "variables": [],
            "notes": [],
        },
    }

    for act in stage2["agent_actions"]:
        u_bind = (str(data_root), run_id, act["action_id"], f"worker-{act['action_id']}")
        bind_action(*u_bind)
        opened = task_open(*u_bind)
        track = opened["task"].get("risk_track")
        _assert(track in {"error_rollback", "concurrency_timing", "resource_lifecycle", "memory_corruption"}, f"missing inline risk_track: {opened['task']}")
        tracks_seen.add(track)
        startup_inputs = {
            p["input_id"]
            for bucket in ("pages", "pending_inputs")
            for p in opened["analysis_startup"][bucket]
        }
        for req_input in ("risk_blueprint", "dts_context", "asset_items"):
            _assert(req_input in startup_inputs, f"Stage 2 analysis_startup missing {req_input}: {sorted(startup_inputs)}")
        raw_task = read_json(Path(act["task_path"]))
        _assert(any(inp["input_id"] == "track_topology" for inp in raw_task["inputs"]), "Stage 2 task must bind track_topology input")
        handoff_path = next(inp["path"] for inp in raw_task["inputs"] if inp["input_id"] == "planning_handoff")
        handoff_doc = read_json(Path(handoff_path))
        _assert(any(r.get("scope") == "global" and "mcp_dts_extraction" in json.dumps(r, ensure_ascii=False) for r in handoff_doc.get("records", [])), f"global relates_to=[] note must reach Stage 2 planning_handoff: {handoff_doc}")
        if track == "memory_corruption":
            topo_path = next(inp["path"] for inp in raw_task["inputs"] if inp["input_id"] == "track_topology")
            track_topo = read_json(Path(topo_path))
            _assert(track_topo["track"] == "memory_corruption", f"unexpected track_topology: {track_topo}")
            _assert(any("memory_anchors" in f for f in track_topo["files"]), f"memory_corruption track_topology missing memory_anchors: {track_topo}")

        records = [
            {
                "kind": "check",
                "body": {
                    "format_version": CHECK_FORMAT_V2,
                    "check_id": f"CHK-{track[:4].upper()}-01",
                    "track": track,
                    "entity_ref": "MEM-SLOT-01" if track == "memory_corruption" else "RES-SLOT",
                    "matched_dts_pattern_ids": [h["dts_id"] for h in homologous],
                    "object": "chan_a 已修复点与 chan_b 同源未修复点核对",
                    "sites": ["mem-repo:src/chan_b.c:1-7"],
                    "hypothesis": "chan_b_teardown 未排空回调释放 slot 后被 chan_b_async_cb 覆写踩内存",
                    "conclusion": "needs_runtime_verification" if track == "memory_corruption" else "excluded",
                    "reasoning": "chan_a.c 已在 6.1.6 合入 cancel_async_cb 与置空保护，不再重报；兄弟路径 chan_b.c 仍裸调 pool_free 且异步回调直接 memcpy 写入 c->slot->payload。",
                    "evidence_quotes": [{"ref": "mem-repo:src/chan_b.c:1-7", "snippet": "memcpy(c->slot->payload, buf, len);"}],
                    "risk_ids": ["R-MEM-612"] if track == "memory_corruption" else [],
                },
            },
            {"kind": "flow", "body": flow_fixture},
            {"kind": "summary", "body": f"{track} 轨道排查完成"},
        ]
        if track == "memory_corruption":
            records.insert(
                1,
                {
                    "kind": "risk",
                    "body": {
                        "format_version": RISK_FORMAT_V3,
                        "risk_id": "R-MEM-612",
                        "track": "memory_corruption",
                        "verification_status": "needs_runtime_verification",
                        "severity": "Critical",
                        "entity_ref": "MEM-SLOT-01",
                        "matched_dts_pattern_ids": [h["dts_id"] for h in homologous],
                        "title": "chan_b 销毁未排空异步回调导致跨线程踩坏重分配内存池槽位",
                        "trigger": "chan_b 并发断连销毁与异步回包同时到达",
                        "interleaving_or_path": [
                            "T1 chan_b_teardown L2 调用 pool_free(c->slot) 归还槽位且未置空",
                            "内存池将该 slot 分配给新会话",
                            "T2 chan_b_async_cb L5 执行 memcpy(c->slot->payload, buf, len) 踩坏新会话数据",
                        ],
                        "system_result": "新会话内存被静默覆写，后续解引用触发随机栈 coredump",
                        "runtime_uncertainty": "取决于底层调度器在 pool_free 前是否隐式屏障等待回调线程退出",
                        "external_observation": "开启毒化填充 0xDEADBEEF 或 ASAN 后触发 heap-use-after-free / magic 校验失败",
                        "exclusion_condition": "销毁前同步排空异步回调并置空 c->slot",
                        "source_evidence": ["mem-repo:src/chan_b.c:1-3", "mem-repo:src/chan_b.c:4-7"],
                        "evidence_quotes": [
                            {"ref": "mem-repo:src/chan_b.c:1-3", "snippet": "pool_free(c->slot);"},
                            {"ref": "mem-repo:src/chan_b.c:4-7", "snippet": "memcpy(c->slot->payload, buf, len);"},
                        ],
                        "instrumentation": {
                            "points": [
                                {
                                    "id": "INJ-MEM-01",
                                    "kind": "memory_poison_or_bound_probe",
                                    "repo_id": "mem-repo",
                                    "path": "src/chan_b.c",
                                    "symbol": "chan_b_teardown",
                                    "action": "在 pool_free 前对 slot 填充毒化字节并延迟 50ms 放大异步回调窗口",
                                    "observe": "chan_b_async_cb 命中毒化写断点或 ASAN 报错",
                                }
                            ]
                        },
                    },
                },
            )
        w_res = result_write(*u_bind, expected_revision=0, records=records)
        work_finish(*u_bind, revision=w_res["revision"])
        settle_action(*u_bind[:3])

    _assert(tracks_seen == {"error_rollback", "concurrency_timing", "resource_lifecycle", "memory_corruption"}, f"missing tracks: {tracks_seen}")
    report_md = (run_dir / "report.md").read_text(encoding="utf-8")
    _assert("四轨风险检查账本（risk-method-v2）" in report_md, "final report missing 4-track ledger header")
    _assert("memory_corruption" in report_md, "final report missing memory_corruption track row")
    _assert("R-MEM-612" in report_md, "final report missing sibling memory stomp risk")


def verify_lsan_alloc_slices_startup_and_dts_fallback(tmp: Path) -> None:
    # 1) Wrapped allocators, C++ new/delete, early-exit macro, overwrite, ownership-out, and noise filtering.
    c_leak_code = """
    static int get_id(int x) { return x + 1; }
    static void list_init(void *p) { (void)p; }

    int parse_cfg(int ver, char **out_name) {
        int id = get_id(ver);
        list_init(&id);
        char *buf = (char *)vos_malloc(128);
        if (!buf) return -1;
        char *tag = g_strdup("cfg");
        CHECK_RET(ver < 0);
        if (ver != 2) {
            return -22;
        }
        buf = (char *)rte_zmalloc(256);
        char *json_str = cJSON_Print(NULL);
        *out_name = tag;
        free(json_str);
        free(buf);
        return 0;
    }

    int cpp_alloc_demo(int bad) {
        int *arr = new int[16];
        if (bad) return -1;
        delete[] arr;
        return 0;
    }
    """
    scanned = scan_file_topology("leak-repo", "src/cfg.cpp", c_leak_code, "c_cpp")
    _assert(scanned["parser"].startswith("tree-sitter"), f"expected tree-sitter parser, got {scanned['parser']}")
    acquire_syms = {a["symbol"] for a in scanned["resource_anchors"] if a["kind"] == "acquire"}
    _assert({"vos_malloc", "g_strdup", "rte_zmalloc", "cJSON_Print", "new"} <= acquire_syms, f"missing wrapped allocators: {acquire_syms}")
    _assert("get_id" not in acquire_syms and "list_init" not in acquire_syms, f"high-noise get/init leaked into acquire: {acquire_syms}")

    slices_by_callee = {s["callee"]: s for s in scanned["alloc_flow_slices"]}
    _assert("vos_malloc" in slices_by_callee, f"missing vos_malloc slice: {slices_by_callee.keys()}")
    vos_slice = slices_by_callee["vos_malloc"]
    _assert(vos_slice["target"] == "buf", f"unexpected target: {vos_slice}")
    _assert(any(ex["kind"] == "macro_exit_candidate" for ex in vos_slice["post_alloc_exits"]), f"missing macro exit candidate: {vos_slice}")
    _assert(any(ex["kind"] == "return" and "-22" in ex["snippet"] for ex in vos_slice["post_alloc_exits"]), f"missing early return exit: {vos_slice}")
    _assert(bool(vos_slice["overwrite_lines"]), f"missing overwrite_lines for buf reassignment: {vos_slice}")
    _assert("new" in slices_by_callee and slices_by_callee["new"]["target"] == "arr", f"missing C++ new slice: {slices_by_callee}")

    # 2) DTS ingestion: LSan -> resource_lifecycle, ASan/null -> memory_corruption, uncategorized MR/diff -> error_rollback, UI typo -> filtered.
    dts_data_root = tmp / "dts-fallback-data"
    dts_csv = tmp / "lsan_and_fallback.csv"
    dts_csv.write_text(
        "dts_id,title,root_cause,mr_url\n"
        "DTS-LSAN-01,LSan报告cfg解析退出告警,parse_cfg提前返回未释放临时堆块,\n"
        "DTS-ASAN-02,ASan触发空指针解引用崩溃,handler未判空导致SIGSEGV,\n"
        "DTS-OTHER-03,状态字段比较写反导致业务异常,比较符号写反,https://codehub.example/org/repo/merge_requests/77\n"
        "DTS-UI-04,界面按钮文案错别字修改,按钮文案拼写错误,\n",
        encoding="utf-8",
    )
    res = ingest_dts_file(dts_data_root, dts_csv)
    _assert("DTS-LSAN-01" in res["imported"], f"LSan ticket not imported: {res}")
    _assert("DTS-ASAN-02" in res["imported"], f"ASan ticket not imported: {res}")
    _assert("DTS-OTHER-03" in res["imported"], f"Code MR ticket should fallback instead of drop: {res}")
    _assert(any(f.get("dts_id") == "DTS-UI-04" for f in res["filtered_out"]), f"UI typo noise should still be filtered: {res}")
    listed = {item["pattern_id"]: item for item in list_dts_patterns(dts_data_root)["items"]}
    _assert(listed["DTS-LSAN-01"]["track"] == "resource_lifecycle", f"wrong LSan track: {listed['DTS-LSAN-01']}")
    _assert(listed["DTS-ASAN-02"]["track"] == "memory_corruption", f"wrong ASan track: {listed['DTS-ASAN-02']}")
    _assert(listed["DTS-OTHER-03"]["track"] == "error_rollback", f"wrong fallback track: {listed['DTS-OTHER-03']}")

    # 3) analysis_startup prioritizes risk-method-v2 rubrics + track_topology ahead of large scene_common_generation.
    from pangea_agent.agent_io import write_json
    from pangea_agent.cli.adapter_api import bind_action, settle_action
    from pangea_agent.cli.run_module_analysis import run_module_analysis
    from pangea_agent.cli.source_first_api import plan_write, task_open, work_finish

    startup_root = tmp / "startup-priority-data"
    repo_root = startup_root / "repositories" / "leak-repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "cfg.cpp").write_text(c_leak_code, encoding="utf-8")
    req_path = tmp / "request_leak_startup.json"
    run_id = "run-leak-startup"
    write_json(
        req_path,
        {
            "workflow_version": "source-first-v1",
            "analysis_profile": "behavior-test-v2",
            "analysis_settings": {"scenario": "risk-analysis", "mode": "depth", "objective": "product-behavior"},
            "data_root": str(startup_root),
            "run_id": run_id,
            "repository": "leak-repo",
            "target": "cfg 泄漏核查",
            "source_scope": ["src"],
        },
    )
    started = run_module_analysis(str(req_path))
    plan_action = started["agent_actions"][0]["action_id"]
    bind_action(str(startup_root), run_id, plan_action, "plan-leak-1")
    p_bind = (str(startup_root), run_id, plan_action, "plan-leak-1")
    pw = plan_write(
        *p_bind,
        expected_revision=0,
        unit={
            "title": "资源配平轨",
            "purpose": "沿 alloc_flow_slices 逐函数核查泄漏；免重复交付模块流程",
            "risk_track": "resource_lifecycle",
            "owned_files": [{"repo_id": "leak-repo", "path": "src/cfg.cpp"}],
            "context_files": [],
        },
    )
    work_finish(*p_bind, revision=pw["revision"])
    settled = settle_action(str(startup_root), run_id, plan_action)
    ana_action = next(a["action_id"] for a in settled["agent_actions"] if a["role"] == "analysis")
    bind_action(str(startup_root), run_id, ana_action, "ana-leak-1")
    opened = task_open(str(startup_root), run_id, ana_action, "ana-leak-1")
    startup_ids = [page["input_id"] for page in opened["analysis_startup"]["pages"]]
    _assert("rubric_scene_risk_v2" in startup_ids, f"risk_v2 rubric squeezed out of startup: {startup_ids}")
    _assert("rubric_c_cpp_risk_v2" in startup_ids, f"c_cpp_risk_v2 rubric squeezed out of startup: {startup_ids}")
    _assert("track_topology" in startup_ids, f"track_topology squeezed out of startup: {startup_ids}")
    topo_page = next(p for p in opened["analysis_startup"]["pages"] if p["input_id"] == "track_topology")
    _assert("alloc_flow_slices" in topo_page["text"], "resource_lifecycle startup topology missing alloc_flow_slices")
    topo_file = startup_root / "runs" / run_id / "inputs" / "risk-topology" / "unit-0001.json"
    topo_full = json.loads(topo_file.read_text(encoding="utf-8"))
    _assert(topo_full["files"] and topo_full["files"][0].get("alloc_flow_slices") and topo_full["files"][0].get("error_slices"),
            f"resource_lifecycle full topology missing alloc_flow_slices or error_slices: {topo_full}")


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        verify_dts_feedback_and_git_diff(tmp)
        verify_tolerant_ast_topology()
        verify_v2_gates_and_ledger(tmp)
        verify_memory_corruption_dts_and_topology(tmp)
        verify_lsan_alloc_slices_startup_and_dts_fallback(tmp)
    print("ALL risk-method-v2 VERIFICATIONS PASSED")


if __name__ == "__main__":
    main()

