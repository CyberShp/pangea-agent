from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .routing import route_after_advance, route_after_contract, route_after_repositories
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


builder = StateGraph(PangeaState)

builder.add_node("load_contract", load_contract)
builder.add_node("resolve_repositories", resolve_repositories)
builder.add_node("locate_module", locate_module)
builder.add_node("index_materials", index_materials)
builder.add_node("build_inventory", build_inventory)
builder.add_node("make_analysis_units", make_analysis_units)
builder.add_node("prepare_worker_tasks", prepare_worker_tasks)
builder.add_node("advance_run", advance_run)
builder.add_node("finalize_report", finalize_report)

builder.add_edge(START, "load_contract")
builder.add_conditional_edges(
    "load_contract",
    route_after_contract,
    {"resolve_repositories": "resolve_repositories", "advance_run": "advance_run"},
)
builder.add_conditional_edges(
    "resolve_repositories",
    route_after_repositories,
    {
        "locate_module": "locate_module",
        "index_materials": "index_materials",
        "build_inventory": "build_inventory",
        "make_analysis_units": "make_analysis_units",
    },
)
builder.add_edge("locate_module", "index_materials")
builder.add_edge("index_materials", "build_inventory")
builder.add_edge("build_inventory", "make_analysis_units")
builder.add_edge("make_analysis_units", "prepare_worker_tasks")
builder.add_edge("prepare_worker_tasks", END)
builder.add_conditional_edges(
    "advance_run",
    route_after_advance,
    {
        "finalize_report": "finalize_report",
        "end": END,
    },
)
builder.add_edge("finalize_report", END)

graph = builder.compile()
