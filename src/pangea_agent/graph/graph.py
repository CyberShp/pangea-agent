from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from pangea_agent.graph.nodes.advance_workflow import advance_workflow
from pangea_agent.graph.nodes.finalize_workflow import finalize_workflow
from pangea_agent.graph.nodes.open_run import open_run
from pangea_agent.graph.nodes.prepare_inputs import prepare_inputs
from pangea_agent.graph.routing import route_after_advance, route_after_open
from pangea_agent.graph.state import PangeaState


builder = StateGraph(PangeaState)
builder.add_node("open_run", open_run)
builder.add_node("prepare_inputs", prepare_inputs)
builder.add_node("advance_workflow", advance_workflow)
builder.add_node("finalize_workflow", finalize_workflow)

builder.add_edge(START, "open_run")
builder.add_conditional_edges(
    "open_run",
    route_after_open,
    {
        "prepare_inputs": "prepare_inputs",
        "advance_workflow": "advance_workflow",
    },
)
builder.add_edge("prepare_inputs", END)
builder.add_conditional_edges(
    "advance_workflow",
    route_after_advance,
    {
        "finalize_workflow": "finalize_workflow",
        "end": END,
    },
)
builder.add_edge("finalize_workflow", END)

graph = builder.compile()
