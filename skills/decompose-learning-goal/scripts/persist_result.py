#!/usr/bin/env python3
"""
File: skills/decompose-learning-goal/scripts/persist_result.py

Purpose:
    Deterministically persist a decompose-learning-goal skill JSON result into
    a SQLite database that is compatible with the project's CLI graph model.

Responsibilities:
    - Resolve the target SQLite path from skill execution context
    - Load JSON either directly or from a fenced Markdown block
    - Initialise the existing CLI schema from src.data.database.SCHEMA_SQL
    - Add skill import metadata tables without changing CLI graph tables
    - Validate title references before writing graph rows
    - Map skill nodes and edges into learning_goals, knowledge_nodes, and
      knowledge_edges

What this file does NOT do:
    - Run decomposition or subagent review
    - Generate SQL from model output
    - Use src.infrastructure.config.DB_PATH for persistence path selection
    - Implement interactive CLI presentation

Inputs:  skill JSON/Markdown, optional db path, optional user_id
Outputs: SQLite rows and a JSON summary printed by the CLI entrypoint
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Resolve the shared runtime (vendored _core/ when installed, repo root in
# development) — see docs/22 and scripts/_bootstrap.py.
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
import _bootstrap  # noqa: E402,F401

from src.data.database import SCHEMA_SQL as CLI_SCHEMA_SQL  # noqa: E402


DEFAULT_DB_FILENAME = "learning.db"
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
VALID_EDGE_TYPES = {"prerequisite", "cross_domain_analogy"}
MASTERY_THRESHOLDS = {
    "critical": 0.95,
    "standard": 0.80,
    "familiarity": 0.60,
}

SKILL_IMPORT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS skill_result_imports (
    id                   TEXT PRIMARY KEY,
    goal_id              TEXT NOT NULL,
    user_id              TEXT NOT NULL DEFAULT 'default',
    target               TEXT NOT NULL,
    content_hash         TEXT NOT NULL,
    review_status        TEXT NOT NULL,
    review_provider      TEXT NOT NULL,
    assumptions          TEXT NOT NULL DEFAULT '[]',
    review_json          TEXT NOT NULL DEFAULT '{}',
    unresolved_questions TEXT NOT NULL DEFAULT '[]',
    raw_json             TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    UNIQUE(user_id, content_hash),
    FOREIGN KEY(goal_id) REFERENCES learning_goals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS skill_result_sources (
    id          TEXT PRIMARY KEY,
    import_id   TEXT NOT NULL REFERENCES skill_result_imports(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    used_for    TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_import_hash
    ON skill_result_imports(content_hash);
CREATE INDEX IF NOT EXISTS idx_skill_import_goal
    ON skill_result_imports(goal_id);
CREATE INDEX IF NOT EXISTS idx_skill_import_review
    ON skill_result_imports(review_status, review_provider);
CREATE INDEX IF NOT EXISTS idx_skill_source_url
    ON skill_result_sources(url);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return str(uuid.uuid4())


def resolve_db_path(db_path: str | Path | None, cwd: Path | None = None) -> Path:
    """
    Resolve where skill persistence should write SQLite data.

    Rules (docs/20, canonical copy in src/infrastructure/config.py):
    - Explicit directory path: that directory / learning.db.
    - Explicit .db/.sqlite/.sqlite3 path: that file.
    - No user path: DB_PATH env var, else <cwd>/learning.db.
    """
    base = (cwd or Path.cwd()).resolve()
    if db_path is None:
        env_path = os.environ.get("DB_PATH")
        if env_path:
            return Path(env_path).expanduser().resolve()
        return base / DEFAULT_DB_FILENAME

    candidate = Path(db_path).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    candidate = candidate.resolve()

    if candidate.suffix.lower() in SQLITE_SUFFIXES:
        return candidate
    return candidate / DEFAULT_DB_FILENAME


def _canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def load_result_text(text: str) -> dict[str, Any]:
    """Parse direct JSON or the first fenced JSON object in Markdown text."""
    stripped = text.strip()
    if stripped.startswith("{"):
        parsed = json.loads(stripped)
    else:
        match = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text)
        if not match:
            raise ValueError("No direct JSON object or fenced ```json block found.")
        parsed = json.loads(match.group(1))

    if not isinstance(parsed, dict):
        raise ValueError("Skill result must be a JSON object.")
    return parsed


def load_result_file(path: Path) -> dict[str, Any]:
    """Load a skill result from a file path or stdin when path is '-'."""
    if str(path) == "-":
        return load_result_text(sys.stdin.read())
    return load_result_text(path.read_text(encoding="utf-8"))


def _list_value(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list.")
    return value


def _string_list(value: Any, field_name: str) -> list[str]:
    return [str(item) for item in _list_value(value, field_name)]


def _node_title(node: dict[str, Any]) -> str:
    title = str(node.get("title", "")).strip()
    if not title:
        raise ValueError("Every node must have a non-empty title.")
    return title


def _parent_title(node: dict[str, Any]) -> str | None:
    parent = node.get("parent_title")
    if parent is None:
        return None
    parent = str(parent).strip()
    return parent or None


def _validate_and_prepare_nodes(data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("nodes must be a non-empty list.")

    titles: set[str] = set()
    prepared: list[dict[str, Any]] = []
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            raise ValueError("Every node must be an object.")
        title = _node_title(raw_node)
        if title in titles:
            raise ValueError(f"Duplicate node title: {title}")
        titles.add(title)
        prepared.append(raw_node)

    roots = [node for node in prepared if _parent_title(node) is None]
    if len(roots) != 1:
        raise ValueError("Exactly one node must have parent_title = null for CLI root_node mapping.")

    children_by_parent: dict[str, list[str]] = {title: [] for title in titles}
    for node in prepared:
        parent_title = _parent_title(node)
        if parent_title is None:
            continue
        if parent_title not in titles:
            raise ValueError(f"Unknown parent_title '{parent_title}' for node '{_node_title(node)}'.")
        children_by_parent[parent_title].append(_node_title(node))

    depth_by_title: dict[str, int] = {}
    visiting: set[str] = set()
    node_by_title = {_node_title(node): node for node in prepared}
    root_title = _node_title(roots[0])

    def assign_depth(title: str, depth: int) -> None:
        if title in visiting:
            raise ValueError(f"Cycle detected in parent_title tree at '{title}'.")
        visiting.add(title)
        depth_by_title[title] = depth
        for child_title in children_by_parent[title]:
            assign_depth(child_title, depth + 1)
        visiting.remove(title)

    assign_depth(root_title, 0)
    missing_from_tree = set(node_by_title) - set(depth_by_title)
    if missing_from_tree:
        missing = ", ".join(sorted(missing_from_tree))
        raise ValueError(f"Nodes are disconnected from the root parent_title tree: {missing}")

    return prepared, depth_by_title


def _is_atomic(node: dict[str, Any]) -> bool:
    return bool(node.get("is_atomic", True))


def _validate_edges(
    data: dict[str, Any],
    node_titles: set[str],
    atomic_by_title: dict[str, bool],
) -> list[dict[str, Any]]:
    edges = data.get("edges", [])
    if not isinstance(edges, list):
        raise ValueError("edges must be a list.")

    prepared: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    prerequisite_adj: dict[str, list[str]] = {
        title: [] for title, is_atomic in atomic_by_title.items() if is_atomic
    }
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("Every edge must be an object.")
        from_title = str(edge.get("from_title", "")).strip()
        to_title = str(edge.get("to_title", "")).strip()
        edge_type = str(edge.get("edge_type", "prerequisite")).strip() or "prerequisite"
        if from_title not in node_titles:
            raise ValueError(f"Unknown edge from_title '{from_title}'.")
        if to_title not in node_titles:
            raise ValueError(f"Unknown edge to_title '{to_title}'.")
        if edge_type not in VALID_EDGE_TYPES:
            raise ValueError(
                f"Invalid edge_type '{edge_type}'. Expected one of: "
                f"{', '.join(sorted(VALID_EDGE_TYPES))}."
            )
        if (from_title, to_title) in seen_pairs:
            raise ValueError(f"Duplicate edge from '{from_title}' to '{to_title}'.")
        seen_pairs.add((from_title, to_title))
        if edge_type == "prerequisite":
            if not atomic_by_title[from_title] or not atomic_by_title[to_title]:
                raise ValueError(
                    "prerequisite edges must connect atomic nodes; use parent_title "
                    "for decomposition hierarchy."
                )
            prerequisite_adj[from_title].append(to_title)
        prepared.append(edge)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(title: str) -> None:
        if title in visiting:
            raise ValueError(f"prerequisite cycle detected at '{title}'.")
        if title in visited:
            return
        visiting.add(title)
        for next_title in prerequisite_adj.get(title, []):
            visit(next_title)
        visiting.remove(title)
        visited.add(title)

    for title in prerequisite_adj:
        visit(title)
    return prepared


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(CLI_SCHEMA_SQL)
    conn.executescript(SKILL_IMPORT_SCHEMA_SQL)


def _insert_node_row(
    conn: sqlite3.Connection,
    *,
    node_id: str,
    node: dict[str, Any],
    goal_id: str,
    parent_node: str | None,
    depth_level: int,
    created_at: str,
) -> None:
    strictness = str(node.get("strictness_level") or "standard")
    mastery_threshold = node.get("mastery_threshold")
    if mastery_threshold is None:
        mastery_threshold = MASTERY_THRESHOLDS.get(strictness, 0.80)
    difficulty = int(node.get("difficulty", 3))
    qa_set = [
        {
            "question": str(question),
            "expected_answer": "",
            "difficulty": difficulty,
        }
        for question in _string_list(node.get("qa_draft", []), "qa_draft")
    ]

    row = {
        "id": node_id,
        "title": _node_title(node),
        "description": str(node.get("description", "")),
        "domain": str(node.get("domain", "")),
        "concept_fingerprint": json.dumps(
            _string_list(node.get("concept_fingerprint", []), "concept_fingerprint"),
            ensure_ascii=False,
        ),
        "difficulty": difficulty,
        "est_minutes": int(node.get("est_minutes", 10)),
        "qa_set": json.dumps(qa_set, ensure_ascii=False),
        "depth_level": depth_level,
        "parent_node": parent_node,
        "goal_id": goal_id,
        "strictness_level": strictness,
        "mastery_threshold": float(mastery_threshold),
        "risk_note": str(node.get("risk_note", "")),
        "is_atomic": 1 if bool(node.get("is_atomic", True)) else 0,
        "created_at": created_at,
    }
    conn.execute(
        """INSERT INTO knowledge_nodes VALUES (
            :id,:title,:description,:domain,:concept_fingerprint,
            :difficulty,:est_minutes,:qa_set,:depth_level,:parent_node,
            :goal_id,:strictness_level,:mastery_threshold,:risk_note,
            :is_atomic,:created_at
        )""",
        row,
    )


def persist_result(
    data: dict[str, Any],
    *,
    db_path: str | Path,
    user_id: str = "default",
) -> dict[str, Any]:
    """
    Persist a skill result and return import summary metadata.

    The operation is idempotent by canonical JSON content hash.
    """
    if not isinstance(data, dict):
        raise ValueError("Skill result must be a JSON object.")

    target = str(data.get("target", "")).strip()
    if not target:
        raise ValueError("target must be a non-empty string.")

    raw_json = _canonical_json(data)
    content_hash = _content_hash(data)
    resolved_db_path = Path(db_path)
    if not resolved_db_path.is_absolute():
        resolved_db_path = resolved_db_path.resolve()

    conn = _connect(resolved_db_path)
    try:
        _init_schema(conn)
        conn.commit()

        nodes, depth_by_title = _validate_and_prepare_nodes(data)
        node_titles = {_node_title(node) for node in nodes}
        atomic_by_title = {_node_title(node): _is_atomic(node) for node in nodes}
        edges = _validate_edges(data, node_titles, atomic_by_title)
        created_at = _now()

        existing = conn.execute(
            "SELECT id, goal_id FROM skill_result_imports WHERE user_id = ? AND content_hash = ?",
            (user_id, content_hash),
        ).fetchone()
        if existing:
            return {
                "db_path": str(resolved_db_path),
                "imported": False,
                "import_id": existing["id"],
                "goal_id": existing["goal_id"],
                "nodes": len(nodes),
                "edges": len(edges),
                "content_hash": content_hash,
            }

        with conn:
            goal_id = _id()
            import_id = _id()
            title_to_id = {_node_title(node): _id() for node in nodes}
            root_node = next(node for node in nodes if _parent_title(node) is None)
            root_id = title_to_id[_node_title(root_node)]

            conn.execute(
                "INSERT INTO learning_goals VALUES (:id,:user_id,:title,:root_node,:status,:created_at)",
                {
                    "id": goal_id,
                    "user_id": user_id,
                    "title": target,
                    "root_node": root_id,
                    "status": "active",
                    "created_at": created_at,
                },
            )

            for node in nodes:
                parent_title = _parent_title(node)
                _insert_node_row(
                    conn,
                    node_id=title_to_id[_node_title(node)],
                    node=node,
                    goal_id=goal_id,
                    parent_node=title_to_id[parent_title] if parent_title else None,
                    depth_level=depth_by_title[_node_title(node)],
                    created_at=created_at,
                )

            for edge in edges:
                conn.execute(
                    """INSERT INTO knowledge_edges VALUES (
                        :id,:from_node,:to_node,:edge_type,:weight,:analogy_desc,:created_at
                    )""",
                    {
                        "id": _id(),
                        "from_node": title_to_id[str(edge.get("from_title", "")).strip()],
                        "to_node": title_to_id[str(edge.get("to_title", "")).strip()],
                        "edge_type": str(edge.get("edge_type", "prerequisite")),
                        "weight": float(edge.get("weight", 1.0)),
                        "analogy_desc": edge.get("analogy_desc"),
                        "created_at": created_at,
                    },
                )

            review = data.get("review") if isinstance(data.get("review"), dict) else {}
            conn.execute(
                """INSERT INTO skill_result_imports VALUES (
                    :id,:goal_id,:user_id,:target,:content_hash,:review_status,
                    :review_provider,:assumptions,:review_json,:unresolved_questions,
                    :raw_json,:created_at
                )""",
                {
                    "id": import_id,
                    "goal_id": goal_id,
                    "user_id": user_id,
                    "target": target,
                    "content_hash": content_hash,
                    "review_status": str(review.get("status", "")),
                    "review_provider": str(review.get("provider", "")),
                    "assumptions": json.dumps(
                        _string_list(data.get("assumptions", []), "assumptions"),
                        ensure_ascii=False,
                    ),
                    "review_json": json.dumps(review, ensure_ascii=False),
                    "unresolved_questions": json.dumps(
                        _string_list(data.get("unresolved_questions", []), "unresolved_questions"),
                        ensure_ascii=False,
                    ),
                    "raw_json": raw_json,
                    "created_at": created_at,
                },
            )

            for source in _list_value(data.get("sources", []), "sources"):
                if not isinstance(source, dict):
                    raise ValueError("Every source must be an object.")
                conn.execute(
                    "INSERT INTO skill_result_sources VALUES (:id,:import_id,:title,:url,:used_for,:created_at)",
                    {
                        "id": _id(),
                        "import_id": import_id,
                        "title": str(source.get("title", "")),
                        "url": str(source.get("url", "")),
                        "used_for": str(source.get("used_for", "")),
                        "created_at": created_at,
                    },
                )
    finally:
        conn.close()

    return {
        "db_path": str(resolved_db_path),
        "imported": True,
        "import_id": import_id,
        "goal_id": goal_id,
        "nodes": len(nodes),
        "edges": len(edges),
        "content_hash": content_hash,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Persist decompose-learning-goal skill JSON into CLI-compatible SQLite."
    )
    parser.add_argument("result", help="Path to JSON/Markdown result file, or '-' for stdin.")
    parser.add_argument(
        "--db",
        dest="db_path",
        default=None,
        help="SQLite file path or directory. Defaults to current project root / learning.db.",
    )
    parser.add_argument("--user", default="default", help="User ID for learning_goals rows.")
    args = parser.parse_args(argv)

    data = load_result_file(Path(args.result))
    db_path = resolve_db_path(args.db_path)
    summary = persist_result(data, db_path=db_path, user_id=args.user)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
