"""Focused report regression; no model or external services required."""
import copy
import json
import random
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pangea_agent.report.flow_diagram import render_flow_svg
from pangea_agent.report.flow_section import _FlowEntry, _flow_body
from pangea_agent.report.source_first import _behavior_markdown, _html, _variant_anchor, _record_markdown
from pangea_agent.report.branch_coverage import coverage_markdown

artifacts = tempfile.TemporaryDirectory(prefix="pangea-report-verification-")
out = Path(artifacts.name)
flow = {"format_version":"behavior-flow-v1","flow_id":"connect","role":"main","layout_version":"stable-v1","title":"连接、重试与在线修改",
        "main_path_ids":["main-connect","main-reconnect"],"attach_to":None,
        "nodes":[{"id":n,"semantic_key":n,"kind":k,"label":label} for n,k,label in [
            ("a","entry","接收连接请求"),("b","branch","协商配置与当前对象状态"),("c","main","执行认证"),("d","exit","连接成功"),("e","error","拒绝连接并清理"),("f","recovery","修改配置后重新连接")]],
        "edges":[{"edge_id":f"edge-{i}","source_step_key":a,"target_step_key":b,"condition":c} for i,(a,b,c) in enumerate([
            ("a","b","请求到达"),("b","c","sha256 + ffdhe2048"),("b","c","sha384 + ffdhe3072"),("b","e","能力无交集、状态不允许或配置更新期间拒绝本次请求；原连接与新连接分别确认"),
            ("c","d","认证成功"),("c","e","认证失败 <script>bad()</script>"),("e","f","用户修改配置并重试"),("f","b","再次协商：长条件 " + "LongIdentifierWithoutSpaces"*5),("d","d","重复请求：保持连接"),("a","d","已存在且复用")],1)],
        "paths":[{"path_id":"main-connect","title":"新建连接","node_ids":["a","b","c","d"],"edge_ids":["edge-1","edge-2","edge-5"]},
                 {"path_id":"main-reconnect","title":"修改后连接","node_ids":["f","b","c","d"],"edge_ids":["edge-8","edge-3","edge-5"]}]}
svg, warnings = render_flow_svg(flow, "sample")
assert not warnings, warnings
# Explicit main_path_ids remains authoritative even without a layout_version.
declared_only = copy.deepcopy(flow)
declared_only.pop("layout_version")
declared_svg, declared_warnings = render_flow_svg(declared_only, "sample")
assert declared_svg and not declared_warnings
from pangea_agent.report.flow_section import _main_path
assert _main_path(declared_only) == _main_path(flow)
no_main = copy.deepcopy(declared_only)
no_main["main_path_ids"] = []
assert _main_path(no_main) == [], "empty declaration must not fall back to paths[0]"
ET.fromstring(svg)
assert "&lt;script&gt;" in svg and "<script>" not in svg
assert "LongIdentifierWithoutSpaces"*5 in ''.join(ET.fromstring(svg).itertext()).replace("\n", "")
for seed in range(30):
    perm = copy.deepcopy(flow)
    for key in ("nodes","edges","paths","main_path_ids"):
        random.Random(seed).shuffle(perm[key])
    assert render_flow_svg(perm,"sample")[0] == svg, seed
assert "edge-2" in svg and "edge-3" in svg
(out / "flow.svg").write_text(svg, encoding="utf-8")

def record(identifier, kind, body, supersedes=None):
    return SimpleNamespace(record_id=identifier, kind=kind, body=body, evidence=[], relates_to=[], supersedes=supersedes or [])
flow_record = record("flow", "flow", flow)
entry = _FlowEntry("u",flow_record,flow,"flow",[])
text = "\n".join(_flow_body(entry, {}, lambda v,u,k:str(v)))
assert "**新建连接**" in text and "**修改后连接**" in text
assert "5. 修改配置" not in text
changed = copy.deepcopy(flow)
changed["main_path_ids"] = []
assert "未声明可读取的主路径" in "\n".join(_flow_body(_FlowEntry("u",flow_record,changed,"flow",[]),{},lambda v,u,k:str(v)))

case = {"format_version":"behavior-test-case-v1","case_id":"C-1","title":"不同配置建链","test_level":"business_blackbox","purpose":"branch","execution_readiness":"ready",
        "steps":[{"action":"配置主机后连接","expected":"连接成功"}],"variants":[{"variant_id":"V-256","input":"sha256 + ffdhe2048","expected":"成功"},{"variant_id":"V-384","input":"sha384 + ffdhe3072","expected":"成功"},"unusual variant retained"]}
ledger = {"format_version":"behavior-coverage-v1","unit_id":"u","scope":"连接模块","combination_strategy":"依赖组合","unresolved_dependencies":[],
          "extra":{"unrecognized":"must remain ``` literal"},"items":[
            {"item_id":"i1","operation":"连接","conditions":["sha256"],"constraints":[],"expected_behavior":"成功","source_evidence":["repo:a.c:20"],"case_refs":[{"case_id":"C-1","variant_id":"V-256"}],"status":"designed","reason":"已关联"},
            {"item_id":"i2","operation":"连接","conditions":["sha384"],"constraints":[],"expected_behavior":"成功","source_evidence":[],"case_refs":[{"case_id":"C-1","variant_id":"V-absent"}],"status":"unresolved","reason":"设施待确认"},
            {"item_id":"i3","status":{"unexpected":"value"},"case_refs":42,"flow_refs":7},"malformed-but-retained"]}
old = copy.deepcopy(ledger); old["scope"]="RETIRED-CONTENT"
records = [record("old","note",old),record("ledger","note",ledger,["old"]),record("case","test_case",case),flow_record]
with tempfile.TemporaryDirectory() as td:
    state={"data_root":td,"run_id":"run-report"}
    run=Path(td)/"runs/run-report"
    (run/"inputs").mkdir(parents=True)
    (run/"inputs/coverage-gaps.json").write_text("[]")
    (run/"inputs/flow-comparison.json").write_text(json.dumps({"baseline":{"run_id":"run-old","version_id":"version-1"},"comparability":{"status":"comparable","dimensions":{"source":"same","model":"unknown"}},"flows":[{"flow_id":"connect","status":"changed","nodes":{"added":[],"removed":[],"changed":[]},"edges":{"added":["edge-3"],"removed":[],"changed":[]},"main_paths_changed":True}]}))
    progress={"run_id":"run-report","lifecycle_status":"complete","quality_status":"PASS"}
    action={"stage":"unit_analysis","status":"accepted","task":{"unit_id":"u"}}
    result=SimpleNamespace(records=records, warnings=[])
    assets={}
    md=_behavior_markdown(state,progress,[(action,result)],assets)
    assert md.count("## 业务覆盖清单") == 1
    assert "RETIRED-CONTENT" not in md
    assert "已设计 1／未设计 0／待确认 1／不适用 0／未知状态 1／无法结构化条目 1" in md
    assert "100%" not in md
    assert "未找到有效变体" in md and "unusual variant retained" in md
    assert "unrecognized" in md and "malformed-but-retained" in md
    target=_variant_anchor("u","case","V-256")
    assert f"](#{target})" in md
    page=_html(md,assets)
    assert f'id="{target}"' in page and f'href="#{target}"' in page
    assert "must remain ``` literal" in page
    assert "<script>bad()" not in page
    assert "流程版本对照" in page
    (out/"report.html").write_text(page,encoding="utf-8")
    (out/"report.md").write_text(md,encoding="utf-8")
    # An accepted closure replaces the old unit deliverable, including its ledger.
    replacement=copy.deepcopy(ledger); replacement["scope"]="CLOSURE-CONTENT"
    closure={"stage":"targeted_closure","status":"accepted","task":{"unit_id":"u"}}
    result2=SimpleNamespace(records=[record("closure-ledger","note",replacement)],warnings=[])
    closed=_behavior_markdown(state,progress,[(action,result),(closure,result2)])
    assert "CLOSURE-CONTENT" in closed and "**业务范围**：连接模块" not in closed

# Ambiguous author IDs never choose an arbitrary target.
with tempfile.TemporaryDirectory() as td:
    state={"data_root":td,"run_id":"duplicates"}
    duplicate_case=copy.deepcopy(case)
    duplicate_case["variants"].append(copy.deepcopy(duplicate_case["variants"][0]))
    result=SimpleNamespace(records=[record("ledger","note",ledger),record("case","test_case",duplicate_case)],warnings=[])
    md=_behavior_markdown(state,{},[(action,result)])
    target=_variant_anchor("u","case","V-256")
    assert "编号不唯一" in md and f"](#{target})" not in md and f"pangea-anchor:{target}" not in md
    result.records=[record("ledger","note",ledger),record("case-one","test_case",case),record("case-two","test_case",copy.deepcopy(case))]
    md=_behavior_markdown(state,{},[(action,result)])
    assert "编号不唯一" in md and "pangea-anchor:" not in md
    assert "输入变体" in md

# Entry identity is a declared layout hint even when a retry points back to it.
cyclic={"layout_version":"stable-v1","nodes":[{"id":"a","semantic_key":"a","label":"处理"},{"id":"z","semantic_key":"z","kind":"entry","label":"入口"}],
        "edges":[{"edge_id":"begin","source_step_key":"z","target_step_key":"a"},{"edge_id":"retry","source_step_key":"a","target_step_key":"z"}],"paths":[],"main_path_ids":[]}
xml=ET.fromstring(render_flow_svg(cyclic,"entry-loop")[0])
ns={"s":"http://www.w3.org/2000/svg"}
ys={g.attrib["data-node-id"]:float(g.find("s:rect",ns).attrib["y"]) for g in xml.findall("s:g[@data-node-id]",ns)}
assert ys["z"] < ys["a"]
# Main ordering remains intact despite a shortcut edge.
xml=ET.fromstring(svg)
ys={g.attrib["data-node-id"]:float(g.find("s:rect",ns).attrib["y"]) for g in xml.findall("s:g[@data-node-id]",ns)}
assert ys["a"] < ys["b"] < ys["c"] < ys["d"]

for items in (None,{},"bad list",[],[None], [7], [{"status":["x"],"case_refs":[None]}]):
    data={"format_version":"behavior-coverage-v1","items":items,"extra":"keep-me"}
    text=coverage_markdown(data, lambda value,key:str(value))
    assert "keep-me" in text
# Writer invokes final-diff publication before assembling the visible report.
from unittest.mock import patch
from pangea_agent.report.source_first import write_source_first_reports
with tempfile.TemporaryDirectory() as td:
    state={"data_root":td,"run_id":"writer","task_contract":{"analysis_profile":"behavior-test-v2"}}
    run=Path(td)/"runs/writer"
    (run/"inputs").mkdir(parents=True)
    action={"action_id":"a","stage":"unit_analysis","status":"accepted","task":{"unit_id":"u"}}
    result=SimpleNamespace(records=[record("case","test_case",case)],warnings=[])
    def publish(state,progress,records):
        (run/"outputs").mkdir()
        (run/"outputs/flow-delivery-comparison.json").write_text(json.dumps({"comparison_scope":"delivery","reviewed":False,"baseline":{"run_id":"parent","version_id":"version"},"comparability":{},"flows":[{"flow_id":"connect","status":"unchanged","content_changed":True,"content_changes":{"changed":[{"identity":"node.label","before":"成功","after":"拒绝"}]}}]}))
        return {"run_id":"writer","version_id":"version-new","eligible":True,"flow_count":1}
    with patch("pangea_agent.report.source_first._action_records",return_value=[(action,result)]), patch("pangea_agent.analysis_scenarios.frozen_scene",return_value=None), patch("pangea_agent.flow_stability.publish_flow_baseline",side_effect=publish):
        paths=write_source_first_reports(state,progress={"lifecycle_status":"complete","quality_status":"PASS"})
    rendered=Path(paths["report_path"]).read_text()
    complete=json.loads((run/"report-complete.json").read_text())
    assert "最终交付修订" in rendered and "结构未变" in rendered and "文字或源码依据" in rendered
    assert '"before": "成功"' in rendered and '"after": "拒绝"' in rendered
    assert complete["flow_baseline"]["version_id"] == "version-new"
    with patch("pangea_agent.report.source_first._action_records",return_value=[(action,result)]), patch("pangea_agent.analysis_scenarios.frozen_scene",return_value=None), patch("pangea_agent.flow_stability.publish_flow_baseline",side_effect=ValueError("metadata pending")):
        paths=write_source_first_reports(state,progress={"lifecycle_status":"complete","quality_status":"PASS"})
    assert "已有分析结果保留" in Path(paths["report_path"]).read_text()

legacy=copy.deepcopy(flow);legacy.pop("layout_version");legacy.pop("main_path_ids")
ET.fromstring(render_flow_svg(legacy,"legacy")[0])
print("PASS: duplicate IDs, entry loop, shortcut rank, permutations(30), multi-main, static SVG escaping/full labels, ledger malformed/unknown, supersession, closure, variant anchors and legacy flow")
artifacts.cleanup()
