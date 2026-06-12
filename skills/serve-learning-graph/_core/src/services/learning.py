"""
File: src/services/learning.py

Purpose:
    Stateless, non-interactive learn-start service. This is the HTTP-facing
    decomposition of agents.teacher.run_chat_loop: it exposes outline + session
    preparation and one Socratic turn as pure request/response steps instead of
    an in-memory REPL.

Responsibilities:
    - Resolve a node by full id or prefix (mirrors the CLI _resolve_node rule).
    - prepare_node: generate or reuse a validated outline, create or resume the
      learning session, load chat history, and report current progress. It marks
      the outline active but NEVER starts an exam.
    - send_message: load the session/node/outline, run one teacher.chat_turn
      (which persists the user+assistant messages, covered sections, and
      progress), and return the reply with the new progress/coverage.
    - Reuse the pure functions in agents.teacher (generate_outline, chat_turn,
      start_or_resume_session) — it does NOT reimplement outline generation,
      Socratic dialogue, or coverage tracking.

What this file does NOT do:
    - Hold server-side memory between calls. Each call is a fresh process; the
      durable state (outline, session, chat history, covered/progress) lives in
      SQLite and is reloaded per request.
    - Call input() / Rich / console output, or run run_chat_loop over HTTP.
    - Start, score, or finalize an exam. At exam-readiness it only *reports*
      exam_ready=True so the UI can surface an entry (doc/14 独立路径).
    - Add DB tables or schema changes.

Key Design Decisions:
    - exam_ready = progress >= EXAM_READY_THRESHOLD (0.9), matching the
      completion threshold in run_chat_loop. It is purely informational; the
      service never auto-navigates from Learn to Exam.
    - Every public function takes an explicit user_id plus a node id/prefix or a
      session id (doc/14 rule). No implicit global resource.
    - outline `sections` may be stored as a JSON string; send_message parses it
      to a list before passing to chat_turn (Module 3's parse-if-str idiom).
    - Node resolution is read-only and done via db.get_connection(), mirroring
      cli/main._resolve_node, to avoid adding a DB helper.

Inputs:
    - node: node id or prefix; user_id: learner id; user_domains: optional list
    - session_id: learning_sessions row id; message: learner free-text turn

Outputs:
    - prepare_node -> {node, outline:{id,sections}, session:{...}, history,
                       progress, exam_ready}
    - send_message -> {response, progress, covered, exam_ready}
"""

from __future__ import annotations

import json
from typing import Any, Optional

from src.agents.teacher import (
    chat_turn,
    generate_outline,
    start_or_resume_session,
)
from src.data import database as db
from src.infrastructure.logger import get_logger

log = get_logger(__name__)

EXAM_READY_THRESHOLD = 0.9  # progress >= this => exam_ready (matches run_chat_loop)


class LearningError(ValueError):
    """Raised for expected learning errors that should become JSON envelopes."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── Resolution helpers ───────────────────────────────────────────────────────────

def _resolve_node(node: str, user_id: str) -> dict:
    """Resolve a node by full id or prefix; mirror cli/main._resolve_node."""
    if not node:
        raise LearningError("node_not_found", "No node id or prefix provided.")
    direct = db.get_node(node)
    if direct:
        return direct
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM knowledge_nodes WHERE id LIKE ? LIMIT 5",
            (f"{node}%",),
        ).fetchall()
    matches = [dict(r) for r in rows]
    if not matches:
        raise LearningError("node_not_found", f"No node id starting with '{node}'.")
    if len(matches) > 1:
        raise LearningError("ambiguous_node", f"Multiple nodes match '{node}'; provide a longer id.")
    return matches[0]


def _get_session(session_id: str, user_id: str) -> dict:
    """Load a learning_sessions row by id (read-only), parsing covered_sections."""
    if not session_id:
        raise LearningError("session_not_found", "No session id provided.")
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM learning_sessions WHERE id=? AND user_id=? LIMIT 1",
            (session_id, user_id),
        ).fetchone()
    if not row:
        raise LearningError("session_not_found", f"No learning session '{session_id}' for user.")
    session = dict(row)
    session["covered_sections"] = _parse_sections(session.get("covered_sections"), default=[])
    return session


def _parse_sections(value: Any, default: Any) -> Any:
    """Parse a value that may be a JSON string into a Python list."""
    if isinstance(value, str):
        try:
            return json.loads(value or "[]")
        except (ValueError, TypeError):
            return default
    if value is None:
        return default
    return value


def _node_view(node: dict) -> dict:
    """Public, JSON-able view of a node row."""
    return {
        "id": node["id"],
        "title": node.get("title", node["id"]),
        "description": node.get("description", ""),
        "domain": node.get("domain", ""),
        "depth_level": node.get("depth_level", 0),
        "mastery_threshold": node.get("mastery_threshold", 0.80),
        "strictness_level": node.get("strictness_level", "standard"),
    }


# ── Public API ───────────────────────────────────────────────────────────────────

def prepare_node(
    node: str,
    user_id: str = "default",
    user_domains: list[str] | None = None,
    model: Optional[str] = None,
) -> dict:
    """
    Resolve the node, generate or reuse its validated outline, create or resume
    the learning session, and load chat history.

    Returns a JSON-able dict. The outline is marked active, but no exam is
    started; exam_ready merely reports whether progress has reached the
    completion threshold.
    """
    node_row = _resolve_node(node, user_id)
    node_id = node_row["id"]

    outline = generate_outline(
        node_id=node_id,
        user_id=user_id,
        model=model,
        user_domains=user_domains,
    )
    sections = _parse_sections(outline.get("sections"), default=[])

    # Mark the outline active so resumed sessions stay consistent with the CLI.
    db.update_outline(outline["id"], status="active")

    session = start_or_resume_session(node_id, outline["id"], user_id)
    covered = _parse_sections(session.get("covered_sections"), default=[])
    progress = float(session.get("progress") or 0.0)

    history_rows = db.get_chat_history(session["id"], limit=100)
    history = [{"role": m["role"], "content": m["content"]} for m in history_rows]

    return {
        "node": _node_view(node_row),
        "outline": {"id": outline["id"], "sections": sections},
        "session": {
            "id": session["id"],
            "covered_sections": covered,
            "progress": progress,
            "status": session.get("status", "active"),
        },
        "history": history,
        "progress": progress,
        "exam_ready": progress >= EXAM_READY_THRESHOLD,
    }


def send_message(
    session_id: str,
    user_id: str,
    message: str,
    model: Optional[str] = None,
) -> dict:
    """
    Run one Socratic turn for an existing session.

    Loads the session, its node, the outline sections (parsed if stored as a
    JSON string), and prior chat history, then delegates to teacher.chat_turn,
    which persists the user+assistant messages, covered sections, and progress.

    Returns {response, progress, covered, exam_ready}. Raises empty_message for
    blank input and session_not_found for an unknown session.
    """
    text = (message or "").strip()
    if not text:
        raise LearningError("empty_message", "Message must not be empty.")

    session = _get_session(session_id, user_id)

    node = db.get_node(session["node_id"])
    if not node:
        raise LearningError("node_not_found", f"Node not found for session: {session['node_id']}")

    outline = db.get_outline(session["node_id"], user_id)
    if not outline:
        raise LearningError("session_not_found", "No outline found for this session's node.")
    sections = _parse_sections(outline.get("sections"), default=[])

    history_rows = db.get_chat_history(session_id, limit=100)
    history = [{"role": m["role"], "content": m["content"]} for m in history_rows]

    response_text, progress, covered = chat_turn(
        session=session,
        node=node,
        outline_sections=sections,
        user_message=text,
        history=history,
        model=model,
    )

    return {
        "response": response_text,
        "progress": progress,
        "covered": covered,
        "exam_ready": progress >= EXAM_READY_THRESHOLD,
    }
