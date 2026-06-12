"""
File: skills/serve-learning-graph/scripts/export_graph.py

Purpose:
    Export a Learning Directed Graph SQLite database to JSON for the Node
    rendering server.

Responsibilities:
    - Resolve the database path and target goal
    - Read goal, node, edge, and user state rows
    - Build a stable parent-child hierarchy payload

What this file does NOT do:
    - Write to SQLite
    - Start an HTTP server
    - Render HTML

Inputs: SQLite DB path, goal ID/prefix, user ID
Outputs: JSON-serialisable graph payload
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


class GoalResolutionError(ValueError):
    """Raised when the requested goal cannot be resolved safely."""


# Deliberate stdlib copy of the canonical rule in src/infrastructure/config.py
# (docs/20): explicit > DB_PATH env > <cwd>/learning.db; an explicit directory
# (no sqlite suffix) means <dir>/learning.db. Pinned together by
# tests/test_db_path_resolution.py.
_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def resolve_db_path(db_path: str | Path | None = None, cwd: Path | None = None) -> Path:
    if db_path:
        candidate = Path(db_path).expanduser().resolve()
        if candidate.suffix.lower() not in _SQLITE_SUFFIXES:
            candidate = candidate / "learning.db"
        return candidate
    env_path = os.environ.get("DB_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return (cwd or Path.cwd()).resolve() / "learning.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _list_goals(conn: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM learning_goals WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def resolve_goal(
    conn: sqlite3.Connection,
    goal_prefix: str | None = None,
    user_id: str = "default",
) -> dict[str, Any]:
    goals = _list_goals(conn, user_id)
    if goal_prefix:
        matches = [goal for goal in goals if goal["id"].startswith(goal_prefix)]
        if not matches:
            raise GoalResolutionError(
                f"No goal found for prefix '{goal_prefix}' and user '{user_id}'."
            )
        if len(matches) > 1:
            ids = ", ".join(goal["id"][:8] for goal in matches)
            raise GoalResolutionError(
                f"Multiple goals match prefix '{goal_prefix}': {ids}."
            )
        return matches[0]

    if not goals:
        raise GoalResolutionError(f"No goal found for user '{user_id}'.")
    if len(goals) > 1:
        ids = ", ".join(f"{goal['id'][:8]}:{goal['title']}" for goal in goals)
        raise GoalResolutionError(f"Multiple goals found; provide --goal. Matches: {ids}")
    return goals[0]


def _load_nodes(conn: sqlite3.Connection, goal_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM knowledge_nodes WHERE goal_id = ? ORDER BY depth_level, created_at, title",
        (goal_id,),
    ).fetchall()
    nodes: list[dict[str, Any]] = []
    for row in rows:
        node = dict(row)
        node["concept_fingerprint"] = _loads(node.get("concept_fingerprint"), [])
        node["qa_set"] = _loads(node.get("qa_set"), [])
        node["is_atomic"] = bool(node.get("is_atomic"))
        nodes.append(node)
    return nodes


def _load_edges(
    conn: sqlite3.Connection,
    node_ids: list[str],
    title_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    if not node_ids:
        return []
    placeholders = ",".join("?" for _ in node_ids)
    rows = conn.execute(
        f"""SELECT * FROM knowledge_edges
            WHERE from_node IN ({placeholders})
              AND to_node IN ({placeholders})
            ORDER BY edge_type, created_at""",
        [*node_ids, *node_ids],
    ).fetchall()
    edges = []
    for row in rows:
        edge = dict(row)
        edge["from_title"] = title_by_id.get(edge["from_node"], edge["from_node"])
        edge["to_title"] = title_by_id.get(edge["to_node"], edge["to_node"])
        edges.append(edge)
    return edges


def _load_states(
    conn: sqlite3.Connection,
    user_id: str,
    node_ids: list[str],
) -> dict[str, dict[str, Any]]:
    if not node_ids:
        return {}
    placeholders = ",".join("?" for _ in node_ids)
    rows = conn.execute(
        f"""SELECT * FROM user_knowledge_state
            WHERE user_id = ? AND node_id IN ({placeholders})""",
        [user_id, *node_ids],
    ).fetchall()
    return {row["node_id"]: dict(row) for row in rows}


def _copy_node(
    node: dict[str, Any],
    state: dict[str, Any] | None,
    prereq_by_to: dict[str, list[dict[str, Any]]],
    dependent_by_from: dict[str, list[dict[str, Any]]],
    analogy_by_node: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    return {
        "id": node["id"],
        "title": node["title"],
        "description": node.get("description") or "",
        "domain": node.get("domain") or "",
        "concept_fingerprint": node.get("concept_fingerprint") or [],
        "difficulty": node.get("difficulty"),
        "est_minutes": node.get("est_minutes"),
        "qa_set": node.get("qa_set") or [],
        "depth_level": node.get("depth_level") or 0,
        "parent_node": node.get("parent_node"),
        "strictness_level": node.get("strictness_level") or "standard",
        "mastery_threshold": node.get("mastery_threshold"),
        "risk_note": node.get("risk_note") or "",
        "is_atomic": bool(node.get("is_atomic")),
        "created_at": node.get("created_at"),
        "state": state,
        "prerequisites": prereq_by_to.get(node["id"], []),
        "dependents": dependent_by_from.get(node["id"], []),
        "analogies": analogy_by_node.get(node["id"], []),
    }


def export_graph(
    db_path: str | Path | None = None,
    goal_prefix: str | None = None,
    user_id: str = "default",
) -> dict[str, Any]:
    resolved_db = resolve_db_path(db_path)
    with _connect(resolved_db) as conn:
        goal = resolve_goal(conn, goal_prefix=goal_prefix, user_id=user_id)
        nodes = _load_nodes(conn, goal["id"])
        node_ids = [node["id"] for node in nodes]
        title_by_id = {node["id"]: node["title"] for node in nodes}
        edges = _load_edges(conn, node_ids, title_by_id)
        states = _load_states(conn, user_id, node_ids)

    children_by_parent: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        children_by_parent[node.get("parent_node")].append(node)
    for children in children_by_parent.values():
        children.sort(key=lambda n: (n.get("depth_level") or 0, n.get("created_at") or "", n["title"]))

    prereq_by_to: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dependent_by_from: dict[str, list[dict[str, Any]]] = defaultdict(list)
    analogy_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        if edge["edge_type"] == "prerequisite":
            prereq_by_to[edge["to_node"]].append(
                {
                    "id": edge["id"],
                    "node_id": edge["from_node"],
                    "title": edge["from_title"],
                    "edge_type": edge["edge_type"],
                }
            )
            dependent_by_from[edge["from_node"]].append(
                {
                    "id": edge["id"],
                    "node_id": edge["to_node"],
                    "title": edge["to_title"],
                    "edge_type": edge["edge_type"],
                }
            )
        elif edge["edge_type"] == "cross_domain_analogy":
            analogy_by_node[edge["to_node"]].append(
                {
                    "id": edge["id"],
                    "node_id": edge["from_node"],
                    "title": edge["from_title"],
                    "edge_type": edge["edge_type"],
                    "direction": "incoming",
                    "analogy_desc": edge.get("analogy_desc"),
                }
            )
            analogy_by_node[edge["from_node"]].append(
                {
                    "id": edge["id"],
                    "node_id": edge["to_node"],
                    "title": edge["to_title"],
                    "edge_type": edge["edge_type"],
                    "direction": "outgoing",
                    "analogy_desc": edge.get("analogy_desc"),
                }
            )
        else:
            dependent_by_from[edge["from_node"]].append(
                {
                    "id": edge["id"],
                    "node_id": edge["to_node"],
                    "title": edge["to_title"],
                    "edge_type": edge["edge_type"],
                    "analogy_desc": edge.get("analogy_desc"),
                }
            )

    node_by_id = {node["id"]: node for node in nodes}
    flat_nodes: list[dict[str, Any]] = []
    visited: set[str] = set()

    def make_tree(node: dict[str, Any]) -> dict[str, Any]:
        copied = _copy_node(
            node,
            states.get(node["id"]),
            prereq_by_to,
            dependent_by_from,
            analogy_by_node,
        )
        if node["id"] in visited:
            copied["children"] = []
            copied["cycle_warning"] = True
            return copied
        visited.add(node["id"])
        copied["children"] = [make_tree(child) for child in children_by_parent.get(node["id"], [])]
        flat_nodes.append(copied)
        return copied

    root_id = goal.get("root_node")
    root_node = node_by_id.get(root_id) if root_id else None
    unattached_trees: list[dict[str, Any]] = []
    if root_node:
        tree = make_tree(root_node)
    else:
        roots = children_by_parent.get(None, [])
        tree = {
            "id": goal["id"],
            "title": goal["title"],
            "description": "",
            "domain": "",
            "depth_level": 0,
            "parent_node": None,
            "is_atomic": False,
            "synthetic": True,
            "children": [make_tree(node) for node in roots],
            "state": None,
            "prerequisites": [],
            "dependents": [],
            "analogies": [],
        }

    for node in nodes:
        if node["id"] not in visited:
            unattached_trees.append(make_tree(node))

    atomic_nodes = [node for node in nodes if node.get("is_atomic")]
    total_minutes = sum(int(node.get("est_minutes") or 0) for node in atomic_nodes)

    return {
        "db_path": str(resolved_db),
        "user_id": user_id,
        "goal": {
            "id": goal["id"],
            "user_id": goal["user_id"],
            "title": goal["title"],
            "root_node": goal.get("root_node"),
            "status": goal.get("status"),
            "created_at": goal.get("created_at"),
        },
        "summary": {
            "node_count": len(nodes),
            "atomic_node_count": len(atomic_nodes),
            "edge_count": len(edges),
            "total_est_minutes": total_minutes,
        },
        "tree": tree,
        "trees": [tree, *unattached_trees],
        "unattached_trees": unattached_trees,
        "nodes": flat_nodes,
        "edges": edges,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a Learning Directed Graph as JSON.")
    parser.add_argument("--db", dest="db_path", help="SQLite DB path")
    parser.add_argument("--goal", dest="goal_prefix", help="Goal ID or prefix")
    parser.add_argument("--user", dest="user_id", default="default", help="User ID")
    parser.add_argument("--output", "-o", dest="output_path", help="Optional JSON output path")
    args = parser.parse_args(argv)

    try:
        payload = export_graph(args.db_path, args.goal_prefix, args.user_id)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.output_path:
            Path(args.output_path).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
