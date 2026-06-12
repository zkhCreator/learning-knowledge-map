"""
File: skills/print-learning-graph/scripts/print_graph.py

Purpose:
    Render a Learning Directed Graph SQLite database as a terminal tree.

Responsibilities:
    - Resolve the database path and target goal
    - Read learning_goals, knowledge_nodes, knowledge_edges, and user state
    - Render the parent-child node hierarchy as plain text

What this file does NOT do:
    - Write to SQLite
    - Start a web server
    - Regenerate or decompose learning goals

Inputs: SQLite DB path, goal ID/prefix, user ID
Outputs: text tree printed to stdout or returned from render_tree()
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


def _status_label(node: dict[str, Any], states: dict[str, dict[str, Any]]) -> str:
    state = states.get(node["id"])
    status = state.get("status") if state else "unknown"
    kind = "A" if int(node.get("is_atomic") or 0) else "G"
    status_code = {
        "mastered": "done",
        "learning": "learn",
        "needs_review": "review",
        "unknown": "new",
    }.get(status, status or "new")
    return f"{kind}/{status_code}"


def render_tree(
    db_path: str | Path | None = None,
    goal_prefix: str | None = None,
    user_id: str = "default",
) -> str:
    resolved_db = resolve_db_path(db_path)
    with _connect(resolved_db) as conn:
        goal = resolve_goal(conn, goal_prefix=goal_prefix, user_id=user_id)
        nodes = _load_nodes(conn, goal["id"])
        node_ids = [node["id"] for node in nodes]
        title_by_id = {node["id"]: node["title"] for node in nodes}
        edges = _load_edges(conn, node_ids, title_by_id)
        states = _load_states(conn, user_id, node_ids)

    node_by_id = {node["id"]: node for node in nodes}
    children_by_parent: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        children_by_parent[node.get("parent_node")].append(node)
    for children in children_by_parent.values():
        children.sort(key=lambda n: (n.get("depth_level") or 0, n.get("created_at") or "", n["title"]))

    prereq_by_to: dict[str, list[str]] = defaultdict(list)
    analogy_by_node: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.get("edge_type") == "prerequisite":
            prereq_by_to[edge["to_node"]].append(edge["from_title"])
        elif edge.get("edge_type") == "cross_domain_analogy":
            analogy_by_node[edge["to_node"]].append(edge["from_title"])
            analogy_by_node[edge["from_node"]].append(edge["to_title"])

    atomic_count = sum(1 for node in nodes if int(node.get("is_atomic") or 0))
    total_minutes = sum(int(node.get("est_minutes") or 0) for node in nodes if int(node.get("is_atomic") or 0))

    lines = [
        f"Learning Graph: {goal['title']} [{goal['status']}]",
        f"Goal ID: {goal['id']}",
        f"DB: {resolved_db}",
        f"Summary: {len(nodes)} nodes, {atomic_count} atomic, {len(edges)} edges, {total_minutes} min",
        "",
    ]

    def node_line(node: dict[str, Any]) -> str:
        minutes = node.get("est_minutes")
        suffix = f" ({minutes} min)" if minutes is not None else ""
        prereqs = prereq_by_to.get(node["id"], [])
        dep_text = f" | prereq: {', '.join(prereqs[:3])}" if prereqs else ""
        if len(prereqs) > 3:
            dep_text += f" +{len(prereqs) - 3}"
        analogies = analogy_by_node.get(node["id"], [])
        analogy_text = f" | analogy: {', '.join(analogies[:3])}" if analogies else ""
        if len(analogies) > 3:
            analogy_text += f" +{len(analogies) - 3}"
        return f"[{_status_label(node, states)}] {node['title']}{suffix}{dep_text}{analogy_text}"

    visited: set[str] = set()

    def render_node(
        node: dict[str, Any],
        prefix: str = "",
        is_last: bool = True,
        path: set[str] | None = None,
    ) -> None:
        connector = "`-- " if is_last else "|-- "
        if path is None:
            path = set()
        if node["id"] in path:
            lines.append(f"{prefix}{connector}[cycle] {node['title']}")
            return
        visited.add(node["id"])
        lines.append(f"{prefix}{connector}{node_line(node)}")
        child_prefix = prefix + ("    " if is_last else "|   ")
        children = children_by_parent.get(node["id"], [])
        for index, child in enumerate(children):
            render_node(child, child_prefix, index == len(children) - 1, {*path, node["id"]})

    root_id = goal.get("root_node")
    root = node_by_id.get(root_id) if root_id else None
    if root:
        lines.append(node_line(root))
        visited.add(root["id"])
        children = children_by_parent.get(root["id"], [])
        for index, child in enumerate(children):
            render_node(child, "", index == len(children) - 1, {root["id"]})
    else:
        roots = children_by_parent.get(None, [])
        if not roots:
            lines.append("(no nodes found)")
        for index, node in enumerate(roots):
            render_node(node, "", index == len(roots) - 1)

    unattached = [node for node in nodes if node["id"] not in visited]
    if unattached:
        lines.extend(["", "Unattached nodes:"])
        for index, node in enumerate(unattached):
            render_node(node, "", index == len(unattached) - 1)

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print a Learning Directed Graph tree.")
    parser.add_argument("--db", dest="db_path", help="SQLite DB path")
    parser.add_argument("--goal", dest="goal_prefix", help="Goal ID or prefix")
    parser.add_argument("--user", dest="user_id", default="default", help="User ID")
    args = parser.parse_args(argv)

    try:
        print(render_tree(args.db_path, args.goal_prefix, args.user_id))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
