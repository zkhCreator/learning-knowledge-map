"""
File: db/database.py

Purpose:
    SQLite database setup, schema creation, and all CRUD operations
    for the learning graph engine.

Responsibilities:
    - Create and manage the SQLite connection
    - Define and migrate the full schema (knowledge_nodes, knowledge_edges,
      user_knowledge_state, learning_goals, review_schedule)
    - Provide typed CRUD helpers used by agents and CLI

What this file does NOT do:
    - Business logic (path finding, scheduling math, agent calls)
    - CLI presentation
    - Ebbinghaus calculations (those live in graph/dag.py)

Inputs:  DB_PATH from config.py
Outputs: sqlite3 connection / row data as plain dicts
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src import config


# ── Connection ─────────────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Return a sqlite3 connection with row_factory set to dict-like access."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Schema ─────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- Learning goals (top-level user objectives)
CREATE TABLE IF NOT EXISTS learning_goals (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    title       TEXT NOT NULL,
    root_node   TEXT,                          -- set after decomposition
    status      TEXT NOT NULL DEFAULT 'decomposing',
    -- 'decomposing' | 'active' | 'completed'
    created_at  TEXT NOT NULL
);

-- Atomic knowledge nodes (DAG vertices)
CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    description         TEXT,
    domain              TEXT,
    concept_fingerprint TEXT,  -- JSON list of abstract tags, e.g. ["隔离性","原子操作"]
    difficulty          INTEGER DEFAULT 3,  -- 1-5
    est_minutes         INTEGER DEFAULT 10,
    qa_set              TEXT,  -- JSON list of {question, expected_answer, difficulty}
    depth_level         INTEGER DEFAULT 0,
    parent_node         TEXT,  -- immediate parent in decompose tree (nullable for root)
    goal_id             TEXT REFERENCES learning_goals(id),
    strictness_level    TEXT NOT NULL DEFAULT 'standard',
    -- 'critical' | 'standard' | 'familiarity'
    mastery_threshold   REAL NOT NULL DEFAULT 0.80,
    risk_note           TEXT,
    is_atomic           INTEGER NOT NULL DEFAULT 1,  -- 1=leaf node, 0=intermediate
    created_at          TEXT NOT NULL
);

-- Directed edges between nodes (DAG edges)
CREATE TABLE IF NOT EXISTS knowledge_edges (
    id              TEXT PRIMARY KEY,
    from_node       TEXT NOT NULL REFERENCES knowledge_nodes(id),
    to_node         TEXT NOT NULL REFERENCES knowledge_nodes(id),
    edge_type       TEXT NOT NULL DEFAULT 'prerequisite',
    -- 'prerequisite' | 'cross_domain_analogy'
    weight          REAL DEFAULT 1.0,
    analogy_desc    TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(from_node, to_node)
);

-- Per-user knowledge state for each node
CREATE TABLE IF NOT EXISTS user_knowledge_state (
    user_id         TEXT NOT NULL DEFAULT 'default',
    node_id         TEXT NOT NULL REFERENCES knowledge_nodes(id),
    status          TEXT NOT NULL DEFAULT 'unknown',
    -- 'unknown' | 'learning' | 'mastered' | 'needs_review'
    raw_score       REAL DEFAULT 0.0,      -- last QA score (0.0-1.0)
    stability       REAL DEFAULT 1.0,      -- ebbinghaus stability factor
    last_reviewed   TEXT,
    next_review     TEXT,
    review_count    INTEGER DEFAULT 0,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (user_id, node_id)
);

-- Ebbinghaus review schedule
CREATE TABLE IF NOT EXISTS review_schedule (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL DEFAULT 'default',
    node_id             TEXT NOT NULL REFERENCES knowledge_nodes(id),
    scheduled_at        TEXT NOT NULL,
    actual_at           TEXT,
    review_round        INTEGER NOT NULL DEFAULT 1,
    score               REAL,
    next_interval_days  INTEGER,
    status              TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' | 'completed' | 'overdue'
    created_at          TEXT NOT NULL
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_nodes_goal    ON knowledge_nodes(goal_id);
CREATE INDEX IF NOT EXISTS idx_edges_from    ON knowledge_edges(from_node);
CREATE INDEX IF NOT EXISTS idx_edges_to      ON knowledge_edges(to_node);
CREATE INDEX IF NOT EXISTS idx_review_user   ON review_schedule(user_id, status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_state_user    ON user_knowledge_state(user_id, status);
"""


def init_db():
    """Create all tables if they don't exist. Safe to call multiple times."""
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)
    print(f"✓ Database ready at {config.DB_PATH}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _id() -> str:
    return str(uuid.uuid4())

def row_to_dict(row) -> dict:
    return dict(row) if row else {}


# ── Learning Goals CRUD ────────────────────────────────────────────────────────

def create_goal(title: str, user_id: str = "default") -> dict:
    goal = {
        "id": _id(),
        "user_id": user_id,
        "title": title,
        "root_node": None,
        "status": "decomposing",
        "created_at": _now(),
    }
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO learning_goals VALUES (:id,:user_id,:title,:root_node,:status,:created_at)",
            goal,
        )
    return goal


def get_goal(goal_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM learning_goals WHERE id = ?", (goal_id,)
        ).fetchone()
    return row_to_dict(row)


def list_goals(user_id: str = "default") -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM learning_goals WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_goal(goal_id: str, **fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE learning_goals SET {sets} WHERE id = ?",
            (*fields.values(), goal_id),
        )


# ── Knowledge Nodes CRUD ───────────────────────────────────────────────────────

def create_node(
    title: str,
    goal_id: str,
    description: str = "",
    domain: str = "",
    concept_fingerprint: list[str] | None = None,
    difficulty: int = 3,
    est_minutes: int = 10,
    qa_set: list[dict] | None = None,
    depth_level: int = 0,
    parent_node: str | None = None,
    strictness_level: str = "standard",
    mastery_threshold: float | None = None,
    risk_note: str = "",
    is_atomic: bool = True,
) -> dict:
    if mastery_threshold is None:
        mastery_threshold = config.MASTERY_THRESHOLDS.get(strictness_level, 0.80)
    node = {
        "id": _id(),
        "title": title,
        "description": description,
        "domain": domain,
        "concept_fingerprint": json.dumps(concept_fingerprint or [], ensure_ascii=False),
        "difficulty": difficulty,
        "est_minutes": est_minutes,
        "qa_set": json.dumps(qa_set or [], ensure_ascii=False),
        "depth_level": depth_level,
        "parent_node": parent_node,
        "goal_id": goal_id,
        "strictness_level": strictness_level,
        "mastery_threshold": mastery_threshold,
        "risk_note": risk_note,
        "is_atomic": 1 if is_atomic else 0,
        "created_at": _now(),
    }
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO knowledge_nodes VALUES (
                :id,:title,:description,:domain,:concept_fingerprint,
                :difficulty,:est_minutes,:qa_set,:depth_level,:parent_node,
                :goal_id,:strictness_level,:mastery_threshold,:risk_note,
                :is_atomic,:created_at
            )""",
            node,
        )
    return node


def get_node(node_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM knowledge_nodes WHERE id = ?", (node_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["concept_fingerprint"] = json.loads(d["concept_fingerprint"] or "[]")
    d["qa_set"] = json.loads(d["qa_set"] or "[]")
    return d


def list_nodes_for_goal(goal_id: str, atomic_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM knowledge_nodes WHERE goal_id = ?"
    params = [goal_id]
    if atomic_only:
        sql += " AND is_atomic = 1"
    sql += " ORDER BY depth_level, created_at"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["concept_fingerprint"] = json.loads(d["concept_fingerprint"] or "[]")
        d["qa_set"] = json.loads(d["qa_set"] or "[]")
        result.append(d)
    return result


# ── Edges CRUD ─────────────────────────────────────────────────────────────────

def create_edge(
    from_node: str,
    to_node: str,
    edge_type: str = "prerequisite",
    weight: float = 1.0,
    analogy_desc: str = "",
) -> dict:
    edge = {
        "id": _id(),
        "from_node": from_node,
        "to_node": to_node,
        "edge_type": edge_type,
        "weight": weight,
        "analogy_desc": analogy_desc,
        "created_at": _now(),
    }
    with get_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO knowledge_edges VALUES (:id,:from_node,:to_node,:edge_type,:weight,:analogy_desc,:created_at)",
                edge,
            )
        except sqlite3.IntegrityError:
            pass  # duplicate edge, ignore
    return edge


def get_prerequisites(node_id: str) -> list[dict]:
    """Return all nodes that must be learned before node_id."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT n.* FROM knowledge_nodes n
               JOIN knowledge_edges e ON e.from_node = n.id
               WHERE e.to_node = ? AND e.edge_type = 'prerequisite'""",
            (node_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_dependents(node_id: str) -> list[dict]:
    """Return all nodes that require node_id as prerequisite."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT n.* FROM knowledge_nodes n
               JOIN knowledge_edges e ON e.to_node = n.id
               WHERE e.from_node = ? AND e.edge_type = 'prerequisite'""",
            (node_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── User Knowledge State CRUD ──────────────────────────────────────────────────

def upsert_state(
    node_id: str,
    status: str,
    raw_score: float = 0.0,
    stability: float = 1.0,
    last_reviewed: str | None = None,
    next_review: str | None = None,
    review_count: int = 0,
    user_id: str = "default",
):
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO user_knowledge_state
               (user_id, node_id, status, raw_score, stability,
                last_reviewed, next_review, review_count, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id, node_id) DO UPDATE SET
                 status=excluded.status,
                 raw_score=excluded.raw_score,
                 stability=excluded.stability,
                 last_reviewed=excluded.last_reviewed,
                 next_review=excluded.next_review,
                 review_count=excluded.review_count,
                 updated_at=excluded.updated_at
            """,
            (user_id, node_id, status, raw_score, stability,
             last_reviewed, next_review, review_count, now),
        )


def get_state(node_id: str, user_id: str = "default") -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM user_knowledge_state WHERE user_id=? AND node_id=?",
            (user_id, node_id),
        ).fetchone()
    return row_to_dict(row)


def list_states(user_id: str = "default", status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM user_knowledge_state WHERE user_id = ?"
    params: list = [user_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── Review Schedule CRUD ───────────────────────────────────────────────────────

def create_review(
    node_id: str,
    scheduled_at: str,
    review_round: int = 1,
    user_id: str = "default",
) -> dict:
    review = {
        "id": _id(),
        "user_id": user_id,
        "node_id": node_id,
        "scheduled_at": scheduled_at,
        "actual_at": None,
        "review_round": review_round,
        "score": None,
        "next_interval_days": None,
        "status": "pending",
        "created_at": _now(),
    }
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO review_schedule VALUES (
                :id,:user_id,:node_id,:scheduled_at,:actual_at,
                :review_round,:score,:next_interval_days,:status,:created_at
            )""",
            review,
        )
    return review


def get_due_reviews(user_id: str = "default") -> list[dict]:
    """Return pending + overdue reviews, ordered by urgency."""
    now = _now()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT r.*, n.title as node_title, n.strictness_level
               FROM review_schedule r
               JOIN knowledge_nodes n ON n.id = r.node_id
               WHERE r.user_id = ? AND r.status = 'pending' AND r.scheduled_at <= ?
               ORDER BY r.scheduled_at ASC""",
            (user_id, now),
        ).fetchall()
    return [dict(r) for r in rows]


def complete_review(review_id: str, score: float, next_interval_days: int):
    with get_connection() as conn:
        conn.execute(
            """UPDATE review_schedule
               SET status='completed', actual_at=?, score=?, next_interval_days=?
               WHERE id=?""",
            (_now(), score, next_interval_days, review_id),
        )
