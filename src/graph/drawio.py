"""
File: graph/drawio.py

Purpose:
    Export a goal's knowledge graph into a draw.io-compatible diagram file.

Responsibilities:
    - Read goal nodes, edges, and optional user state from the database
    - Compute a stable, readable node layout for the decomposition tree
    - Generate plain XML that can be opened directly by draw.io / diagrams.net

What this file does NOT do:
    - CLI argument parsing
    - Database writes
    - Perfect graph layout optimisation

Inputs:  goal_id/user_id and graph data from db/database.py
Outputs: draw.io XML strings and exported .drawio files
"""

from __future__ import annotations

import html
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from src.db import database as db
from src.graph import dag

_NODE_WIDTH = 240
_NODE_HEIGHT = 92
_X_GAP = 320
_Y_GAP = 140
_LEFT_MARGIN = 80
_TOP_MARGIN = 80


def export_goal_to_drawio(
    goal_id: str,
    output_path: str | Path,
    user_id: str = "default",
    atomic_only: bool = False,
) -> Path:
    """Write a draw.io file for the selected goal and return the final path."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        build_goal_drawio_xml(goal_id=goal_id, user_id=user_id, atomic_only=atomic_only),
        encoding="utf-8",
    )
    return output


def build_goal_drawio_xml(
    goal_id: str,
    user_id: str = "default",
    atomic_only: bool = False,
) -> str:
    """Return a draw.io XML document for a goal graph."""
    goal = db.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal not found: {goal_id}")

    nodes = db.list_nodes_for_goal(goal_id, atomic_only=atomic_only)
    if not nodes:
        raise ValueError(f"No nodes found for goal: {goal_id}")

    with db.get_connection() as conn:
        node_ids = [n["id"] for n in nodes]
        state_rows = conn.execute(
            "SELECT * FROM user_knowledge_state WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        edge_rows = conn.execute(
            """SELECT * FROM knowledge_edges
               WHERE from_node IN ({0}) AND to_node IN ({0})
               ORDER BY created_at ASC""".format(",".join("?" for _ in node_ids)),
            [*node_ids, *node_ids],
        ).fetchall()

    state_map = {row["node_id"]: dict(row) for row in state_rows}
    positions = _layout_nodes(goal=goal, nodes=nodes)

    mxfile = ET.Element(
        "mxfile",
        {
            "host": "app.diagrams.net",
            "modified": datetime.now(timezone.utc).isoformat(),
            "agent": "Learning Directed Graph CLI",
            "version": "24.7.17",
        },
    )
    diagram = ET.SubElement(
        mxfile,
        "diagram",
        {"id": str(uuid.uuid4()), "name": goal["title"][:120] or "Goal Map"},
    )
    model = ET.SubElement(
        diagram,
        "mxGraphModel",
        {
            "dx": "1320",
            "dy": "760",
            "grid": "1",
            "gridSize": "10",
            "guides": "1",
            "tooltips": "1",
            "connect": "1",
            "arrows": "1",
            "fold": "1",
            "page": "1",
            "pageScale": "1",
            "pageWidth": "1600",
            "pageHeight": "1200",
            "math": "0",
            "shadow": "0",
        },
    )
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})

    node_cells: dict[str, str] = {}
    for index, node in enumerate(nodes, start=2):
        cell_id = f"n{index}"
        node_cells[node["id"]] = cell_id
        x, y = positions[node["id"]]
        cell = ET.SubElement(
            root,
            "mxCell",
            {
                "id": cell_id,
                "value": _node_label(node, state_map.get(node["id"])),
                "style": _node_style(node, state_map.get(node["id"])),
                "vertex": "1",
                "parent": "1",
            },
        )
        ET.SubElement(
            cell,
            "mxGeometry",
            {
                "x": str(x),
                "y": str(y),
                "width": str(_NODE_WIDTH),
                "height": str(_NODE_HEIGHT),
                "as": "geometry",
            },
        )

    next_edge_index = len(nodes) + 2
    for node in nodes:
        parent_id = node.get("parent_node")
        if parent_id and parent_id in node_cells:
            cell = ET.SubElement(
                root,
                "mxCell",
                {
                    "id": f"e{next_edge_index}",
                    "value": "",
                    "style": _tree_edge_style(),
                    "edge": "1",
                    "parent": "1",
                    "source": node_cells[parent_id],
                    "target": node_cells[node["id"]],
                },
            )
            ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
            next_edge_index += 1

    for edge in edge_rows:
        if edge["from_node"] not in node_cells or edge["to_node"] not in node_cells:
            continue
        if _is_tree_edge(edge, nodes):
            continue
        cell = ET.SubElement(
            root,
            "mxCell",
            {
                "id": f"e{next_edge_index}",
                "value": edge["edge_type"],
                "style": _graph_edge_style(edge["edge_type"]),
                "edge": "1",
                "parent": "1",
                "source": node_cells[edge["from_node"]],
                "target": node_cells[edge["to_node"]],
            },
        )
        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
        next_edge_index += 1

    return ET.tostring(mxfile, encoding="unicode")


def default_drawio_path(goal: dict) -> Path:
    """Return the default export path for a goal."""
    safe_title = _safe_filename(goal["title"]) or "goal-map"
    return Path("exports") / f"{goal['id'][:8]}-{safe_title}.drawio"


def _layout_nodes(goal: dict, nodes: list[dict]) -> dict[str, tuple[int, int]]:
    node_map = {node["id"]: node for node in nodes}
    children_map: dict[str | None, list[dict]] = {}
    for node in nodes:
        parent_id = node.get("parent_node")
        children_map.setdefault(parent_id, []).append(node)

    for children in children_map.values():
        children.sort(key=lambda n: (n.get("depth_level", 0), n.get("created_at", ""), n["title"]))

    ordered_roots: list[dict] = []
    root_id = goal.get("root_node")
    if root_id and root_id in node_map:
        ordered_roots.append(node_map[root_id])
    for node in children_map.get(None, []):
        if node["id"] not in {root["id"] for root in ordered_roots}:
            ordered_roots.append(node)
    for node in nodes:
        if node["id"] not in {root["id"] for root in ordered_roots} and node.get("parent_node") not in node_map:
            ordered_roots.append(node)

    positions: dict[str, tuple[int, int]] = {}
    next_row = 0

    def place(node: dict, depth: int) -> int:
        nonlocal next_row
        children = [child for child in children_map.get(node["id"], []) if child["id"] in node_map]
        if not children:
            y = _TOP_MARGIN + next_row * _Y_GAP
            next_row += 1
        else:
            child_ys = [place(child, depth + 1) for child in children]
            y = int(sum(child_ys) / len(child_ys))
        x = _LEFT_MARGIN + depth * _X_GAP
        positions[node["id"]] = (x, y)
        return y

    for index, root in enumerate(ordered_roots):
        if index > 0 and next_row > 0:
            next_row += 1
        place(root, 0)

    for node in nodes:
        if node["id"] not in positions:
            x = _LEFT_MARGIN + node.get("depth_level", 0) * _X_GAP
            y = _TOP_MARGIN + next_row * _Y_GAP
            positions[node["id"]] = (x, y)
            next_row += 1

    return positions


def _node_label(node: dict, state: dict | None) -> str:
    status = state.get("status", "unknown") if state else "unknown"
    title = html.escape(node["title"])
    domain = html.escape(node.get("domain") or "未分类")
    strictness = html.escape(node.get("strictness_level", "standard"))
    node_type = "atomic" if node.get("is_atomic") else "intermediate"
    if status == "mastered" and state:
        mastery = dag.effective_mastery(
            state.get("raw_score", 0.0),
            state.get("stability", 1.0),
            state.get("last_reviewed"),
        )
        status_text = f"mastered {mastery:.0%}"
    else:
        status_text = status

    meta = " | ".join(
        [
            domain,
            f"{node.get('est_minutes', '?')} min",
            strictness,
            node_type,
            status_text,
        ]
    )
    return f"<b>{title}</b><br/><font style=\"font-size:11px;color:#666666;\">{meta}</font>"


def _node_style(node: dict, state: dict | None) -> str:
    fill = "#dae8fc" if not node.get("is_atomic") else "#ffffff"
    stroke = "#6c8ebf" if not node.get("is_atomic") else "#666666"
    status = state.get("status", "unknown") if state else "unknown"
    if status == "mastered":
        fill = "#d5e8d4"
        stroke = "#82b366"
    elif status == "learning":
        fill = "#fff2cc"
        stroke = "#d6b656"
    elif status == "needs_review":
        fill = "#f8cecc"
        stroke = "#b85450"

    return (
        "rounded=1;whiteSpace=wrap;html=1;"
        "align=left;verticalAlign=middle;spacing=12;"
        f"fillColor={fill};strokeColor={stroke};strokeWidth=1.5;"
        "fontSize=14;fontStyle=1;shadow=0;"
    )


def _tree_edge_style() -> str:
    return (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;"
        "jettySize=auto;html=1;endArrow=none;strokeColor=#6c8ebf;strokeWidth=1.5;"
    )


def _graph_edge_style(edge_type: str) -> str:
    if edge_type == "cross_domain_analogy":
        return (
            "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;"
            "html=1;dashed=1;dashPattern=8 4;strokeColor=#9673a6;endArrow=open;"
            "fontSize=11;labelBackgroundColor=#ffffff;"
        )
    return (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;"
        "html=1;dashed=1;dashPattern=4 4;strokeColor=#666666;endArrow=classic;"
        "fontSize=11;labelBackgroundColor=#ffffff;"
    )


def _is_tree_edge(edge: dict, nodes: list[dict]) -> bool:
    node_map = {node["id"]: node for node in nodes}
    target = node_map.get(edge["to_node"])
    return bool(target and target.get("parent_node") == edge["from_node"])


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip()
    return cleaned[:80]
