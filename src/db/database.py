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

-- ── Learning phase tables ──────────────────────────────────────────────────

-- Knowledge outlines (generated per node for learning phase)
CREATE TABLE IF NOT EXISTS node_outlines (
    id          TEXT PRIMARY KEY,
    node_id     TEXT NOT NULL REFERENCES knowledge_nodes(id),
    user_id     TEXT NOT NULL DEFAULT 'default',
    sections    TEXT NOT NULL,  -- JSON: [{index, title, content, sources[], analogy, analogy_source_node, covered}]
    status      TEXT NOT NULL DEFAULT 'draft',
    -- 'draft' | 'validated' | 'active' | 'completed'
    created_at  TEXT NOT NULL,
    UNIQUE(node_id, user_id)
);

-- Learning sessions (Socratic dialogue sessions)
CREATE TABLE IF NOT EXISTS learning_sessions (
    id              TEXT PRIMARY KEY,
    node_id         TEXT NOT NULL REFERENCES knowledge_nodes(id),
    outline_id      TEXT NOT NULL REFERENCES node_outlines(id),
    user_id         TEXT NOT NULL DEFAULT 'default',
    progress        REAL NOT NULL DEFAULT 0.0,    -- 0.0-1.0 (fraction of sections covered)
    covered_sections TEXT NOT NULL DEFAULT '[]',   -- JSON: [1, 3, 4] (section indexes)
    status          TEXT NOT NULL DEFAULT 'active',
    -- 'active' | 'summarised' | 'completed'
    started_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Chat messages within a learning session
CREATE TABLE IF NOT EXISTS chat_messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES learning_sessions(id),
    role        TEXT NOT NULL,   -- 'user' | 'assistant'
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- ── Exam phase tables ─────────────────────────────────────────────────────

-- Exam attempts
CREATE TABLE IF NOT EXISTS exam_attempts (
    id          TEXT PRIMARY KEY,
    node_id     TEXT NOT NULL REFERENCES knowledge_nodes(id),
    outline_id  TEXT REFERENCES node_outlines(id),
    user_id     TEXT NOT NULL DEFAULT 'default',
    total_score REAL,              -- average score across all questions
    passed      INTEGER,           -- 1=passed, 0=failed
    started_at  TEXT NOT NULL,
    finished_at TEXT
);

-- Individual exam questions
CREATE TABLE IF NOT EXISTS exam_questions (
    id              TEXT PRIMARY KEY,
    exam_id         TEXT NOT NULL REFERENCES exam_attempts(id),
    question_type   TEXT NOT NULL DEFAULT 'short_answer',
    -- 'multiple_choice' | 'short_answer' | 'scenario' | 'distinction'
    question        TEXT NOT NULL,
    options         TEXT,          -- JSON: for multiple_choice
    expected_answer TEXT NOT NULL,
    user_answer     TEXT,
    score           REAL,          -- 0.0-1.0
    source_section  INTEGER,       -- outline section index this Q came from (null if expansion)
    is_expansion    INTEGER NOT NULL DEFAULT 0,  -- 1=expanded beyond outline
    created_at      TEXT NOT NULL
);

-- Error notebook entries
CREATE TABLE IF NOT EXISTS error_notebook (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL DEFAULT 'default',
    node_id             TEXT NOT NULL REFERENCES knowledge_nodes(id),
    exam_id             TEXT NOT NULL REFERENCES exam_attempts(id),
    question_id         TEXT NOT NULL REFERENCES exam_questions(id),
    source_section_title TEXT,         -- which outline section
    error_type          TEXT NOT NULL,
    -- 'memory_confusion' | 'boundary_unclear' | 'fundamental_misunderstanding' | 'incomplete'
    question            TEXT NOT NULL,
    user_answer         TEXT NOT NULL,
    correct_answer      TEXT NOT NULL,
    explanation         TEXT,          -- why the answer was wrong + how to fix
    related_node_ids    TEXT,          -- JSON: IDs of related knowledge nodes
    related_node_titles TEXT,          -- JSON: titles for display
    review_count        INTEGER DEFAULT 0,
    last_reviewed       TEXT,
    created_at          TEXT NOT NULL
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_nodes_goal    ON knowledge_nodes(goal_id);
CREATE INDEX IF NOT EXISTS idx_edges_from    ON knowledge_edges(from_node);
CREATE INDEX IF NOT EXISTS idx_edges_to      ON knowledge_edges(to_node);
CREATE INDEX IF NOT EXISTS idx_review_user   ON review_schedule(user_id, status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_state_user    ON user_knowledge_state(user_id, status);
CREATE INDEX IF NOT EXISTS idx_outline_node  ON node_outlines(node_id, user_id);
CREATE INDEX IF NOT EXISTS idx_session_node  ON learning_sessions(node_id, user_id, status);
CREATE INDEX IF NOT EXISTS idx_chat_session  ON chat_messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_exam_node     ON exam_attempts(node_id, user_id);
CREATE INDEX IF NOT EXISTS idx_errors_user   ON error_notebook(user_id, node_id);

-- ── Mnemonic strategy layer tables ───────────────────────────────────────

-- User cognitive preference profile
CREATE TABLE IF NOT EXISTS user_cognitive_profile (
    user_id          TEXT PRIMARY KEY,
    spatial_weight   REAL DEFAULT 0.33,
    symbolic_weight  REAL DEFAULT 0.33,
    narrative_weight REAL DEFAULT 0.34,
    assessed         INTEGER DEFAULT 0,   -- 0=not assessed, 1=assessed
    updated_at       TEXT NOT NULL
);

-- Mnemonic anchors (one per section per node per user)
CREATE TABLE IF NOT EXISTS mnemonic_anchors (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    node_id         TEXT NOT NULL REFERENCES knowledge_nodes(id),
    strategy        TEXT NOT NULL,           -- 'spatial' | 'symbolic' | 'narrative'
    section_index   INTEGER,                 -- outline section index, NULL = goal-level
    content         TEXT NOT NULL,            -- mnemonic description text
    palace_location TEXT,                     -- spatial strategy only
    effectiveness   REAL,                     -- 0.0-1.0, updated during review
    created_at      TEXT NOT NULL,
    UNIQUE(user_id, node_id, section_index)
);

-- Palace layouts (one per goal per user, spatial strategy only)
CREATE TABLE IF NOT EXISTS palace_layouts (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    goal_id         TEXT NOT NULL REFERENCES learning_goals(id),
    layout_desc     TEXT NOT NULL,            -- spatial layout description
    location_map    TEXT,                     -- JSON: {node_id: location_name}
    created_at      TEXT NOT NULL,
    UNIQUE(user_id, goal_id)
);

CREATE INDEX IF NOT EXISTS idx_anchor_node   ON mnemonic_anchors(user_id, node_id);
CREATE INDEX IF NOT EXISTS idx_palace_goal   ON palace_layouts(user_id, goal_id);
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


def delete_goal(goal_id: str) -> dict:
    """
    Delete a goal and every row that depends on its nodes.

    This is implemented as a manual cascade so it works for existing SQLite
    databases even though the current schema does not declare ON DELETE CASCADE.
    """
    deleted = {
        "goals": 0,
        "nodes": 0,
        "edges": 0,
        "states": 0,
        "reviews": 0,
    }

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM learning_goals WHERE id = ?", (goal_id,)
        ).fetchone()
        if not row:
            return deleted

        node_rows = conn.execute(
            "SELECT id FROM knowledge_nodes WHERE goal_id = ?",
            (goal_id,),
        ).fetchall()
        node_ids = [r["id"] for r in node_rows]

        if node_ids:
            placeholders = ",".join("?" for _ in node_ids)

            deleted["reviews"] = conn.execute(
                f"DELETE FROM review_schedule WHERE node_id IN ({placeholders})",
                node_ids,
            ).rowcount
            deleted["states"] = conn.execute(
                f"DELETE FROM user_knowledge_state WHERE node_id IN ({placeholders})",
                node_ids,
            ).rowcount
            deleted["edges"] = conn.execute(
                f"""DELETE FROM knowledge_edges
                    WHERE from_node IN ({placeholders})
                       OR to_node IN ({placeholders})""",
                [*node_ids, *node_ids],
            ).rowcount
            deleted["nodes"] = conn.execute(
                "DELETE FROM knowledge_nodes WHERE goal_id = ?",
                (goal_id,),
            ).rowcount

        deleted["goals"] = conn.execute(
            "DELETE FROM learning_goals WHERE id = ?",
            (goal_id,),
        ).rowcount

    return deleted


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


# ── Node Outlines CRUD ────────────────────────────────────────────────────────

def create_outline(node_id: str, sections: list[dict], user_id: str = "default") -> dict:
    outline = {
        "id": _id(),
        "node_id": node_id,
        "user_id": user_id,
        "sections": json.dumps(sections, ensure_ascii=False),
        "status": "draft",
        "created_at": _now(),
    }
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO node_outlines
               VALUES (:id,:node_id,:user_id,:sections,:status,:created_at)""",
            outline,
        )
    return outline


def get_outline(node_id: str, user_id: str = "default") -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM node_outlines WHERE node_id=? AND user_id=? ORDER BY created_at DESC LIMIT 1",
            (node_id, user_id),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["sections"] = json.loads(d["sections"] or "[]")
    return d


def update_outline(outline_id: str, **fields):
    if "sections" in fields and isinstance(fields["sections"], list):
        fields["sections"] = json.dumps(fields["sections"], ensure_ascii=False)
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE node_outlines SET {sets} WHERE id = ?",
            (*fields.values(), outline_id),
        )


# ── Learning Sessions CRUD ────────────────────────────────────────────────────

def create_learning_session(node_id: str, outline_id: str, user_id: str = "default") -> dict:
    now = _now()
    session = {
        "id": _id(),
        "node_id": node_id,
        "outline_id": outline_id,
        "user_id": user_id,
        "progress": 0.0,
        "covered_sections": json.dumps([]),
        "status": "active",
        "started_at": now,
        "updated_at": now,
    }
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO learning_sessions VALUES (
                :id,:node_id,:outline_id,:user_id,:progress,
                :covered_sections,:status,:started_at,:updated_at
            )""",
            session,
        )
    return session


def get_active_session(node_id: str, user_id: str = "default") -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM learning_sessions WHERE node_id=? AND user_id=? AND status='active' LIMIT 1",
            (node_id, user_id),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["covered_sections"] = json.loads(d["covered_sections"] or "[]")
    return d


def update_session(session_id: str, covered_sections: list[int], progress: float, status: str | None = None):
    fields = {
        "covered_sections": json.dumps(covered_sections),
        "progress": progress,
        "updated_at": _now(),
    }
    if status:
        fields["status"] = status
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE learning_sessions SET {sets} WHERE id = ?",
            (*fields.values(), session_id),
        )


# ── Chat Messages CRUD ────────────────────────────────────────────────────────

def add_chat_message(session_id: str, role: str, content: str) -> dict:
    msg = {"id": _id(), "session_id": session_id, "role": role, "content": content, "created_at": _now()}
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_messages VALUES (:id,:session_id,:role,:content,:created_at)", msg
        )
    return msg


def get_chat_history(session_id: str, limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE session_id=? ORDER BY created_at ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Exam CRUD ──────────────────────────────────────────────────────────────────

def create_exam(node_id: str, outline_id: str | None = None, user_id: str = "default") -> dict:
    exam = {
        "id": _id(),
        "node_id": node_id,
        "outline_id": outline_id,
        "user_id": user_id,
        "total_score": None,
        "passed": None,
        "started_at": _now(),
        "finished_at": None,
    }
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO exam_attempts VALUES (:id,:node_id,:outline_id,:user_id,:total_score,:passed,:started_at,:finished_at)",
            exam,
        )
    return exam


def add_exam_question(
    exam_id: str,
    question: str,
    expected_answer: str,
    question_type: str = "short_answer",
    options: list | None = None,
    source_section: int | None = None,
    is_expansion: bool = False,
) -> dict:
    q = {
        "id": _id(),
        "exam_id": exam_id,
        "question_type": question_type,
        "question": question,
        "options": json.dumps(options, ensure_ascii=False) if options else None,
        "expected_answer": expected_answer,
        "user_answer": None,
        "score": None,
        "source_section": source_section,
        "is_expansion": 1 if is_expansion else 0,
        "created_at": _now(),
    }
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO exam_questions VALUES (
                :id,:exam_id,:question_type,:question,:options,:expected_answer,
                :user_answer,:score,:source_section,:is_expansion,:created_at
            )""",
            q,
        )
    return q


def answer_exam_question(question_id: str, user_answer: str, score: float):
    with get_connection() as conn:
        conn.execute(
            "UPDATE exam_questions SET user_answer=?, score=? WHERE id=?",
            (user_answer, score, question_id),
        )


def finish_exam(exam_id: str, total_score: float, passed: bool):
    with get_connection() as conn:
        conn.execute(
            "UPDATE exam_attempts SET total_score=?, passed=?, finished_at=? WHERE id=?",
            (total_score, 1 if passed else 0, _now(), exam_id),
        )


def get_exam_questions(exam_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM exam_questions WHERE exam_id=? ORDER BY created_at", (exam_id,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("options"):
            d["options"] = json.loads(d["options"])
        result.append(d)
    return result


# ── Error Notebook CRUD ────────────────────────────────────────────────────────

def add_error(
    node_id: str,
    exam_id: str,
    question_id: str,
    source_section_title: str,
    error_type: str,
    question: str,
    user_answer: str,
    correct_answer: str,
    explanation: str = "",
    related_node_ids: list[str] | None = None,
    related_node_titles: list[str] | None = None,
    user_id: str = "default",
) -> dict:
    entry = {
        "id": _id(),
        "user_id": user_id,
        "node_id": node_id,
        "exam_id": exam_id,
        "question_id": question_id,
        "source_section_title": source_section_title,
        "error_type": error_type,
        "question": question,
        "user_answer": user_answer,
        "correct_answer": correct_answer,
        "explanation": explanation,
        "related_node_ids": json.dumps(related_node_ids or [], ensure_ascii=False),
        "related_node_titles": json.dumps(related_node_titles or [], ensure_ascii=False),
        "review_count": 0,
        "last_reviewed": None,
        "created_at": _now(),
    }
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO error_notebook VALUES (
                :id,:user_id,:node_id,:exam_id,:question_id,:source_section_title,
                :error_type,:question,:user_answer,:correct_answer,:explanation,
                :related_node_ids,:related_node_titles,:review_count,:last_reviewed,:created_at
            )""",
            entry,
        )
    return entry


def list_errors(
    user_id: str = "default",
    node_id: str | None = None,
    error_type: str | None = None,
) -> list[dict]:
    sql = "SELECT e.*, n.title as node_title FROM error_notebook e JOIN knowledge_nodes n ON n.id = e.node_id WHERE e.user_id = ?"
    params: list = [user_id]
    if node_id:
        sql += " AND e.node_id = ?"
        params.append(node_id)
    if error_type:
        sql += " AND e.error_type = ?"
        params.append(error_type)
    sql += " ORDER BY e.created_at DESC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["related_node_ids"] = json.loads(d["related_node_ids"] or "[]")
        d["related_node_titles"] = json.loads(d["related_node_titles"] or "[]")
        result.append(d)
    return result


def get_errors_for_node(node_id: str, user_id: str = "default") -> list[dict]:
    """Get error notebook entries for a specific node (used during Ebbinghaus review)."""
    return list_errors(user_id=user_id, node_id=node_id)


# ── Cognitive Profile CRUD ────────────────────────────────────────────────────

def create_cognitive_profile(
    user_id: str,
    spatial_weight: float = 0.33,
    symbolic_weight: float = 0.33,
    narrative_weight: float = 0.34,
    assessed: bool = False,
) -> dict:
    profile = {
        "user_id": user_id,
        "spatial_weight": spatial_weight,
        "symbolic_weight": symbolic_weight,
        "narrative_weight": narrative_weight,
        "assessed": 1 if assessed else 0,
        "updated_at": _now(),
    }
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO user_cognitive_profile
               VALUES (:user_id,:spatial_weight,:symbolic_weight,:narrative_weight,:assessed,:updated_at)""",
            profile,
        )
    profile["assessed"] = assessed
    return profile


def get_cognitive_profile(user_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM user_cognitive_profile WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["assessed"] = bool(d["assessed"])
    return d


def update_cognitive_profile(user_id: str, **fields):
    if "assessed" in fields:
        fields["assessed"] = 1 if fields["assessed"] else 0
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE user_cognitive_profile SET {sets} WHERE user_id = ?",
            (*fields.values(), user_id),
        )


# ── Mnemonic Anchors CRUD ────────────────────────────────────────────────────

def create_mnemonic_anchor(
    user_id: str,
    node_id: str,
    strategy: str,
    section_index: int | None,
    content: str,
    palace_location: str | None = None,
) -> dict:
    anchor = {
        "id": _id(),
        "user_id": user_id,
        "node_id": node_id,
        "strategy": strategy,
        "section_index": section_index,
        "content": content,
        "palace_location": palace_location,
        "effectiveness": None,
        "created_at": _now(),
    }
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO mnemonic_anchors
               VALUES (:id,:user_id,:node_id,:strategy,:section_index,
                       :content,:palace_location,:effectiveness,:created_at)""",
            anchor,
        )
    return anchor


def get_mnemonic_anchors(node_id: str, user_id: str = "default") -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM mnemonic_anchors WHERE node_id=? AND user_id=? ORDER BY section_index",
            (node_id, user_id),
        ).fetchall()
    return [dict(r) for r in rows]


def update_mnemonic_anchor(anchor_id: str, **fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE mnemonic_anchors SET {sets} WHERE id = ?",
            (*fields.values(), anchor_id),
        )


def delete_mnemonic_anchors(node_id: str, user_id: str = "default") -> int:
    """Delete all mnemonic anchors for a node. Returns count of deleted rows."""
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM mnemonic_anchors WHERE node_id=? AND user_id=?",
            (node_id, user_id),
        )
    return cursor.rowcount


# ── Palace Layouts CRUD ───────────────────────────────────────────────────────

def create_palace_layout(
    user_id: str,
    goal_id: str,
    layout_desc: str,
    location_map: dict | None = None,
) -> dict:
    layout = {
        "id": _id(),
        "user_id": user_id,
        "goal_id": goal_id,
        "layout_desc": layout_desc,
        "location_map": json.dumps(location_map or {}, ensure_ascii=False),
        "created_at": _now(),
    }
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO palace_layouts
               VALUES (:id,:user_id,:goal_id,:layout_desc,:location_map,:created_at)""",
            layout,
        )
    layout["location_map"] = location_map or {}
    return layout


def get_palace_layout(goal_id: str, user_id: str = "default") -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM palace_layouts WHERE goal_id=? AND user_id=?",
            (goal_id, user_id),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["location_map"] = json.loads(d["location_map"] or "{}")
    return d


def update_palace_layout(layout_id: str, **fields):
    if "location_map" in fields and isinstance(fields["location_map"], dict):
        fields["location_map"] = json.dumps(fields["location_map"], ensure_ascii=False)
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE palace_layouts SET {sets} WHERE id = ?",
            (*fields.values(), layout_id),
        )
