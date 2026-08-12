from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .routing import route_after_quality_gate
from .state import PangeaState
from .nodes.load_contract import load_contract
from .nodes.resolve_repositories import resolve_repositories
from .nodes.locate_module import locate_module
from .nodes.index_materials import index_materials
from .nodes.build_inventory import build_inventory
from .nodes.make_analysis_units import make_analysis_units
from .nodes.analyze_unit import analyze_unit
from .nodes.assemble_risks import assemble_risks
from .nodes.generate_test_points import generate_test_points
from .nodes.generate_test_cases import generate_test_cases
from .nodes.quality_gate import quality_gate
from .nodes.finalize_report import finalize_report


builder = StateGraph(PangeaState)

builder.add_node("load_contract", load_contract)
builder.add_node("resolve_repositories", resolve_repositories)
builder.add_node("locate_module", locate_module)
builder.add_node("index_materials", index_materials)
builder.add_node("build_inventory", build_inventory)
builder.add_node("make_analysis_units", make_analysis_units)
builder.add_node("analyze_unit", analyze_unit)
builder.add_node("assemble_risks", assemble_risks)
builder.add_node("generate_test_points", generate_test_points)
builder.add_node("generate_test_cases", generate_test_cases)
builder.add_node("quality_gate", quality_gate)
builder.add_node("finalize_report", finalize_report)

builder.add_edge(START, "load_contract")
builder.add_edge("load_contract", "resolve_repositories")
builder.add_edge("resolve_repositories", "locate_module")
builder.add_edge("locate_module", "index_materials")
builder.add_edge("index_materials", "build_inventory")
builder.add_edge("build_inventory", "make_analysis_units")
builder.add_edge("make_analysis_units", "analyze_unit")
builder.add_edge("analyze_unit", "assemble_risks")
builder.add_edge("assemble_risks", "generate_test_points")
builder.add_edge("generate_test_points", "generate_test_cases")
builder.add_edge("generate_test_cases", "quality_gate")
builder.add_conditional_edges(
    "quality_gate",
    route_after_quality_gate,
    {
        "generate_test_cases": "generate_test_cases",
        "finalize_report": "finalize_report",
    },
)
builder.add_edge("finalize_report", END)

graph = builder.compile()
