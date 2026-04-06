"""
File: graph/dag.py

Purpose:
    DAG traversal, topological sorting, path finding, and Ebbinghaus
    mastery score calculations.

Responsibilities:
    - Compute effective mastery score (raw_score with time decay)
    - Determine if a node is considered "complete" for a user
    - Build a topological learning order from the DAG
    - Print a text-based tree view of the knowledge graph in the CLI

What this file does NOT do:
    - Database writes (use db/database.py)
    - Agent calls
    - CLI output formatting beyond simple tree printing

Inputs:  node/edge data from db/database.py, user state dicts
Outputs: ordered lists of nodes, mastery floats, tree strings
"""

import math
from datetime import datetime, timezone
from typing import Optional

from src import config
from src.db import database as db


# ── Mastery Calculations ───────────────────────────────────────────────────────

def effective_mastery(
    raw_score: float,
    stability: float,
    last_reviewed: Optional[str],
) -> float:
    """
    Compute the current effective mastery score using Ebbinghaus decay.

        effective = raw_score × exp(-days_elapsed / stability)

    If the node has never been reviewed, returns 0.0.
    """
    if not last_reviewed or raw_score == 0:
        return 0.0
    try:
        reviewed_at = datetime.fromisoformat(last_reviewed)
        now = datetime.now(timezone.utc)
        if reviewed_at.tzinfo is None:
            reviewed_at = reviewed_at.replace(tzinfo=timezone.utc)
        days_elapsed = (now - reviewed_at).total_seconds() / 86400
    except Exception:
        return raw_score  # If we can't parse, return raw score

    return raw_score * math.exp(-days_elapsed / max(stability, 0.1))


def is_node_complete(node: dict, state: Optional[dict]) -> bool:
    """
    Return True if the user has mastered this node above its threshold.
    Uses effective_mastery (time-decayed score).
    """
    if state is None or state.get("status") not in ("mastered",):
        return False
    em = effective_mastery(
        raw_score=state.get("raw_score", 0.0),
        stability=state.get("stability", 1.0),
        last_reviewed=state.get("last_reviewed"),
    )
    return em >= node.get("mastery_threshold", 0.80)


def next_stability(current: float, score: float, threshold: float) -> float:
    """
    Update stability after a review session.
    Success → stability grows (slower forgetting).
    Failure → stability shrinks.
    """
    if score >= threshold:
        return current * 1.5
    else:
        return max(current * 0.7, 0.1)


def next_review_interval(review_round: int, score: float, threshold: float) -> int:
    """
    Return the number of days until the next review.
    Adjusts based on score relative to threshold.
    """
    intervals = config.REVIEW_INTERVALS
    idx = min(review_round - 1, len(intervals) - 1)
    base_days = intervals[idx]

    if score < 0.5:
        # Failed badly: reset to round 1
        return intervals[0]
    elif score < threshold:
        # Partial: cut interval in half
        return max(1, base_days // 2)
    else:
        return base_days


# ── Topological Sort ───────────────────────────────────────────────────────────

def topological_order(goal_id: str) -> list[dict]:
    """
    Return atomic nodes for a goal in topological order (prerequisites first).
    Non-atomic (intermediate) nodes are excluded from the learning order.
    """
    nodes = db.list_nodes_for_goal(goal_id, atomic_only=True)
    if not nodes:
        return []

    node_map = {n["id"]: n for n in nodes}
    node_ids = set(node_map.keys())

    # Build in-degree and adjacency for atomic nodes only
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}

    with db.get_connection() as conn:
        edges = conn.execute(
            """SELECT from_node, to_node FROM knowledge_edges
               WHERE edge_type = 'prerequisite'"""
        ).fetchall()

    for edge in edges:
        frm, to = edge["from_node"], edge["to_node"]
        if frm in node_ids and to in node_ids:
            adj[frm].append(to)
            in_degree[to] += 1

    # Kahn's algorithm
    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    order: list[dict] = []

    while queue:
        queue.sort(key=lambda nid: node_map[nid].get("depth_level", 0))
        nid = queue.pop(0)
        order.append(node_map[nid])
        for neighbor in adj.get(nid, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # Append any remaining nodes (in case of cycles, shouldn't happen)
    visited = {n["id"] for n in order}
    for n in nodes:
        if n["id"] not in visited:
            order.append(n)

    return order


# ── Text Tree Visualisation ────────────────────────────────────────────────────

def print_tree(goal_id: str, user_id: str = "default") -> str:
    """
    Return a text-based tree of the knowledge graph for a goal,
    annotated with the user's mastery status.

    Example output:
        学会 Kubernetes 集群管理
        ├── [✓] 容器基础概念  (10 min)
        ├── [ ] Pod 生命周期   (12 min)
        │   └── [✓] 容器基础概念  <prereq>
        └── [ ] Service 网络   (15 min)
    """
    all_nodes = db.list_nodes_for_goal(goal_id)
    if not all_nodes:
        return "(no nodes found)"

    node_map = {n["id"]: n for n in all_nodes}

    # Get user state for all nodes
    with db.get_connection() as conn:
        state_rows = conn.execute(
            "SELECT * FROM user_knowledge_state WHERE user_id = ?", (user_id,)
        ).fetchall()
    state_map = {r["node_id"]: dict(r) for r in state_rows}

    # Get goal info
    goal = db.get_goal(goal_id)
    root_id = goal.get("root_node") if goal else None

    lines: list[str] = []
    if goal:
        lines.append(f"📚 {goal['title']}  [{goal['status']}]")

    def _icon(node: dict) -> str:
        state = state_map.get(node["id"])
        if state is None:
            return "○"
        s = state.get("status", "unknown")
        if s == "mastered":
            em = effective_mastery(
                state.get("raw_score", 0), state.get("stability", 1), state.get("last_reviewed")
            )
            return "✓" if em >= node.get("mastery_threshold", 0.8) else "↻"
        if s == "learning":
            return "→"
        if s == "needs_review":
            return "!"
        return "○"

    def _render(node_id: str, prefix: str, is_last: bool, depth: int):
        node = node_map.get(node_id)
        if not node:
            return
        connector = "└── " if is_last else "├── "
        icon = _icon(node)
        atomic_mark = "" if node.get("is_atomic") else "◆ "
        lines.append(
            f"{prefix}{connector}[{icon}] {atomic_mark}{node['title']}  "
            f"({node.get('est_minutes', '?')} min)"
        )
        child_prefix = prefix + ("    " if is_last else "│   ")
        # Find children
        children = [n for n in all_nodes if n.get("parent_node") == node_id]
        for i, child in enumerate(children):
            _render(child["id"], child_prefix, i == len(children) - 1, depth + 1)

    if root_id:
        _render(root_id, "", True, 0)
    else:
        for n in all_nodes:
            if not n.get("parent_node"):
                _render(n["id"], "", True, 0)

    return "\n".join(lines)
