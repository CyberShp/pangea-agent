from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .routing import route_after_advance, route_after_contract, route_after_stage
from .state import PangeaState
from .nodes.load_contract import load_contract
from .nodes.resolve_repositories import resolve_repositories
from .nodes.locate_module import locate_module
from .nodes.index_materials import index_materials
from .nodes.build_inventory import build_inventory
from .nodes.make_analysis_units import make_analysis_units
from .nodes.advance_run import advance_run
from .nodes.prepare_worker_tasks import prepare_worker_tasks
from .nodes.finalize_report import finalize_report
from .nodes.apply_run_event import apply_run_event
from .nodes.workflow_stages import (
    accept_comparison_review,
    accept_independent_review,
    accept_rework,
    accept_rework_verification,
    accept_risk_analysis,
    accept_source_checkpoint,
    accept_test_generation,
    prepare_comparison_review,
    prepare_independent_review,
    prepare_rework,
    prepare_rework_verification,
    prepare_risk_analysis,
    prepare_test_generation,
)


builder = StateGraph(PangeaState)

builder.add_node("load_contract", load_contract)
builder.add_node("resolve_repositories", resolve_repositories)
builder.add_node("locate_module", locate_module)
builder.add_node("index_materials", index_materials)
builder.add_node("build_inventory", build_inventory)
builder.add_node("make_analysis_units", make_analysis_units)
builder.add_node("prepare_worker_tasks", prepare_worker_tasks)
builder.add_node("resume_terminal", advance_run)
builder.add_node("accept_source_checkpoint", accept_source_checkpoint)
builder.add_node("prepare_risk_analysis", prepare_risk_analysis)
builder.add_node("accept_risk_analysis", accept_risk_analysis)
builder.add_node("prepare_test_generation", prepare_test_generation)
builder.add_node("accept_test_generation", accept_test_generation)
builder.add_node("prepare_independent_review", prepare_independent_review)
builder.add_node("accept_independent_review", accept_independent_review)
builder.add_node("prepare_comparison_review", prepare_comparison_review)
builder.add_node("accept_comparison_review", accept_comparison_review)
builder.add_node("prepare_rework", prepare_rework)
builder.add_node("accept_rework", accept_rework)
builder.add_node("prepare_rework_verification", prepare_rework_verification)
builder.add_node("accept_rework_verification", accept_rework_verification)
builder.add_node("finalize_report", finalize_report)
builder.add_node("apply_run_event", apply_run_event)

builder.add_edge(START, "load_contract")
builder.add_conditional_edges(
    "load_contract",
    route_after_contract,
    {
        "resolve_repositories": "resolve_repositories",
        "index_materials": "index_materials",
        "build_inventory": "build_inventory",
        "make_analysis_units": "make_analysis_units",
        "accept_source_checkpoint": "accept_source_checkpoint",
        "accept_risk_analysis": "accept_risk_analysis",
        "accept_test_generation": "accept_test_generation",
        "accept_independent_review": "accept_independent_review",
        "accept_comparison_review": "accept_comparison_review",
        "accept_rework": "accept_rework",
        "accept_rework_verification": "accept_rework_verification",
        "resume_terminal": "resume_terminal",
        "apply_run_event": "apply_run_event",
    },
)
builder.add_edge("resolve_repositories", "locate_module")
builder.add_edge("locate_module", "index_materials")
builder.add_edge("index_materials", "build_inventory")
builder.add_edge("build_inventory", "make_analysis_units")
builder.add_edge("make_analysis_units", "prepare_worker_tasks")
builder.add_edge("prepare_worker_tasks", END)
builder.add_conditional_edges(
    "accept_source_checkpoint",
    route_after_stage,
    {"prepare_risk_analysis": "prepare_risk_analysis", "end": END},
)
builder.add_conditional_edges(
    "accept_risk_analysis",
    route_after_stage,
    {"prepare_test_generation": "prepare_test_generation", "end": END},
)
builder.add_conditional_edges(
    "accept_test_generation",
    route_after_stage,
    {"prepare_independent_review": "prepare_independent_review", "end": END},
)
builder.add_conditional_edges(
    "accept_independent_review",
    route_after_stage,
    {
        "prepare_comparison_review": "prepare_comparison_review",
        "finalize_report": "finalize_report",
        "end": END,
    },
)
builder.add_conditional_edges(
    "accept_comparison_review",
    route_after_stage,
    {
        "prepare_rework": "prepare_rework",
        "finalize_report": "finalize_report",
        "end": END,
    },
)
builder.add_conditional_edges(
    "accept_rework",
    route_after_stage,
    {
        "prepare_rework_verification": "prepare_rework_verification",
        "finalize_report": "finalize_report",
        "end": END,
    },
)
builder.add_conditional_edges(
    "accept_rework_verification",
    route_after_stage,
    {"finalize_report": "finalize_report", "end": END},
)
for node in (
    "prepare_risk_analysis",
    "prepare_test_generation",
    "prepare_independent_review",
    "prepare_comparison_review",
    "prepare_rework",
    "prepare_rework_verification",
):
    builder.add_edge(node, END)
builder.add_conditional_edges(
    "resume_terminal",
    route_after_advance,
    {
        "finalize_report": "finalize_report",
        "end": END,
    },
)
builder.add_edge("finalize_report", END)
builder.add_edge("apply_run_event", END)

graph = builder.compile()
