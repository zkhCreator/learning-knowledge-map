"""
File: src/services/exam.py

Purpose:
    Stateless, non-interactive exam-start service. This is the HTTP-facing
    decomposition of agents.examiner.run_exam_loop: it exposes exam creation +
    question persistence, per-answer scoring, and exam finalization as three
    pure request/response steps instead of an in-memory REPL.

Responsibilities:
    - Resolve a node by full id or prefix (mirrors the CLI _resolve_node rule).
    - start_exam: load or generate the outline (fallback to empty sections like
      run_exam_loop), generate questions, create the exam attempt, and persist
      each question with its server-side expected_answer. Returns a client-safe
      view that NEVER leaks expected_answer.
    - answer_question: reload the authoritative question row from SQLite, score
      it via examiner.score_answer using the node's strictness, persist the
      score + user_answer, and return the full scoring result for the UI.
    - finish_exam: reload all persisted question rows (authoritative score /
      user_answer / question / expected_answer / source_section), merge the
      client-carried qualitative error metadata, and delegate to
      examiner._finalize_exam — which writes user_knowledge_state, the
      review_schedule entry (on pass) and error_notebook rows. The total score
      is computed from DB-persisted scores (tamper-resistant); only the
      qualitative error metadata comes from the client.
    - Reuse the pure functions in agents.examiner / agents.teacher — it does NOT
      reimplement question generation, scoring, or finalization, and never runs
      run_exam_loop.

What this file does NOT do:
    - Hold server-side memory between calls. Each call is a fresh process; the
      durable exam state (attempt, questions, scores) lives in SQLite and is
      reloaded per request. Only qualitative error metadata is client-carried.
    - Call input() / Rich / console output, or run run_exam_loop over HTTP.
    - Leak expected_answer to the browser (it stays in the DB for scoring).
    - Add DB tables or schema changes.

Key Design Decisions:
    - The total score is recomputed from the DB-persisted per-question scores,
      so a client cannot inflate its grade; only the per-question error_type /
      explanation / related_concepts (purely qualitative) are client-supplied.
    - Every public function takes an explicit user_id plus the exam / question /
      node ids (doc/14 rule). No implicit global resource.
    - Outline handling mirrors run_exam_loop: reuse get_outline if present, else
      try teacher.generate_outline, else fall back to empty sections.
    - Node resolution is read-only via db.get_connection(), mirroring
      cli/main._resolve_node and the Module 3/4 services.

Inputs:
    - node: node id or prefix; user_id: learner id; model: optional LLM model
    - exam_id / question_id: durable SQLite row ids
    - user_answer: free-text answer to a question
    - question_meta: {question_id: {error_type, explanation, related_concepts}}

Outputs:
    - start_exam     -> {exam_id, node:{...}, questions:[...], total}
    - answer_question-> {question_id, score, error_type, explanation,
                         related_concepts}
    - finish_exam    -> {total_score, passed, threshold, interval_days,
                         next_review, weak_sections}
"""

from __future__ import annotations

import json
from typing import Any, Optional

from src.agents.examiner import generate_questions, score_answer, _finalize_exam
from src.agents.teacher import generate_outline
from src.data import database as db
from src.infrastructure.logger import get_logger

log = get_logger(__name__)


class ExamError(ValueError):
    """Raised for expected exam errors that should become JSON envelopes."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── Resolution helpers ───────────────────────────────────────────────────────────

def _resolve_node(node: str, user_id: str) -> dict:
    """Resolve a node by full id or prefix; mirror cli/main._resolve_node."""
    if not node:
        raise ExamError("node_not_found", "No node id or prefix provided.")
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
        raise ExamError("node_not_found", f"No node id starting with '{node}'.")
    if len(matches) > 1:
        raise ExamError("ambiguous_node", f"Multiple nodes match '{node}'; provide a longer id.")
    return matches[0]


def _get_exam(exam_id: str, user_id: str) -> dict:
    """Load an exam_attempts row by id (read-only), scoped to the user."""
    if not exam_id:
        raise ExamError("exam_not_found", "No exam id provided.")
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM exam_attempts WHERE id=? AND user_id=? LIMIT 1",
            (exam_id, user_id),
        ).fetchone()
    if not row:
        raise ExamError("exam_not_found", f"No exam '{exam_id}' for user.")
    return dict(row)


def _node_view(node: dict) -> dict:
    """Client-safe view of the node under exam."""
    return {
        "id": node["id"],
        "title": node.get("title", node["id"]),
        "strictness_level": node.get("strictness_level", "standard"),
        "mastery_threshold": node.get("mastery_threshold", 0.80),
    }


def _question_view(row: dict, index: int) -> dict:
    """Client-safe view of a persisted question — NEVER includes expected_answer."""
    options = row.get("options")
    if isinstance(options, str) and options:
        try:
            options = json.loads(options)
        except (ValueError, TypeError):
            options = None
    return {
        "id": row["id"],
        "index": index,
        "question": row["question"],
        "question_type": row.get("question_type", "short_answer"),
        "options": options,
        "source_section": row.get("source_section"),
        "is_expansion": bool(row.get("is_expansion")),
    }


def _load_outline_sections(node_id: str, user_id: str, model: Optional[str]) -> tuple[Optional[str], list[dict]]:
    """
    Load or generate the outline for a node, mirroring run_exam_loop.

    Returns (outline_id, sections). Falls back to (None, []) if no outline
    exists and generation fails.
    """
    outline = db.get_outline(node_id, user_id)
    if outline:
        sections = outline.get("sections")
        if isinstance(sections, str):
            try:
                sections = json.loads(sections or "[]")
            except (ValueError, TypeError):
                sections = []
        return outline.get("id"), sections or []

    try:
        outline = generate_outline(node_id=node_id, user_id=user_id, model=model)
        sections = outline.get("sections")
        if isinstance(sections, str):
            try:
                sections = json.loads(sections or "[]")
            except (ValueError, TypeError):
                sections = []
        return outline.get("id"), sections or []
    except Exception as exc:  # mirror run_exam_loop's best-effort fallback
        log.warning("Outline generation failed for node %s (%s); examining without outline.", node_id, exc)
        return None, []


# ── Public API ───────────────────────────────────────────────────────────────────

def start_exam(node: str, user_id: str = "default", model: Optional[str] = None) -> dict:
    """
    Create an exam attempt for a node: resolve the node, load/generate the
    outline, generate questions, and persist each one server-side.

    Returns a JSON-able, client-safe dict. expected_answer is persisted to the
    DB for scoring but is NEVER returned to the client.
    """
    node_row = _resolve_node(node, user_id)
    node_id = node_row["id"]

    outline_id, sections = _load_outline_sections(node_id, user_id, model)

    questions_data = generate_questions(node_row, sections, model=model)

    exam = db.create_exam(node_id=node_id, outline_id=outline_id, user_id=user_id)

    questions: list[dict] = []
    for i, qd in enumerate(questions_data, 1):
        q_row = db.add_exam_question(
            exam_id=exam["id"],
            question=qd["question"],
            expected_answer=qd["expected_answer"],
            question_type=qd.get("question_type", "short_answer"),
            options=qd.get("options"),
            source_section=qd.get("source_section"),
            is_expansion=bool(qd.get("is_expansion", False)),
        )
        questions.append(_question_view(q_row, i))

    return {
        "exam_id": exam["id"],
        "node": _node_view(node_row),
        "questions": questions,
        "total": len(questions),
    }


def answer_question(
    exam_id: str,
    question_id: str,
    user_id: str,
    user_answer: str,
    model: Optional[str] = None,
) -> dict:
    """
    Score a single answer against the DB-authoritative question/expected_answer
    and persist the score + user_answer.

    Empty answers are treated as "（未作答）" (matching run_exam_loop). Returns
    the full scoring result so the UI can show per-question feedback.
    """
    exam = _get_exam(exam_id, user_id)
    node = db.get_node(exam["node_id"])
    if not node:
        raise ExamError("node_not_found", f"Node not found for exam: {exam['node_id']}")

    rows = db.get_exam_questions(exam_id)
    question = next((q for q in rows if q["id"] == question_id), None)
    if question is None:
        raise ExamError("question_not_found", f"No question '{question_id}' in exam '{exam_id}'.")

    answer = (user_answer or "").strip() or "（未作答）"

    scoring = score_answer(
        question=question["question"],
        expected_answer=question["expected_answer"],
        user_answer=answer,
        strictness=node.get("strictness_level", "standard"),
        model=model,
    )
    score_val = float(scoring["score"])
    db.answer_exam_question(question_id, user_answer=answer, score=score_val)

    return {
        "question_id": question_id,
        "score": score_val,
        "error_type": scoring.get("error_type"),
        "explanation": scoring.get("explanation", ""),
        "related_concepts": scoring.get("related_concepts", []),
    }


def get_exam_view(exam_id: str, user_id: str = "default") -> dict:
    """
    Load a persisted exam for display in the GUI data plane: questions plus any
    raw answers already recorded. This is pure data — no LLM is called and
    expected_answer is NEVER returned.

    Used by the web host to render a tool-generated exam so the learner can
    answer it; scoring/finalization happen back in Codex / Claude Code.
    """
    exam = _get_exam(exam_id, user_id)
    node = db.get_node(exam["node_id"])
    if not node:
        raise ExamError("node_not_found", f"Node not found for exam: {exam['node_id']}")

    rows = db.get_exam_questions(exam_id)
    questions: list[dict] = []
    for i, row in enumerate(rows, 1):
        view = _question_view(row, i)
        # Echo back what the learner has already recorded (but never the answer key).
        view["user_answer"] = row.get("user_answer")
        view["score"] = row.get("score")
        questions.append(view)

    return {
        "exam_id": exam["id"],
        "node": _node_view(node),
        "questions": questions,
        "total": len(questions),
        "finished": bool(exam.get("finished_at")),
    }


def record_answer(
    exam_id: str,
    question_id: str,
    user_id: str,
    user_answer: str,
) -> dict:
    """
    Persist the learner's raw answer without scoring it. This is the GUI data
    plane's "answer and save" step; the LLM-based scoring is deferred to the
    tool. The score column is left NULL until the tool scores the attempt.

    Empty answers are normalized to "（未作答）" to match the scoring flow.
    """
    exam = _get_exam(exam_id, user_id)
    rows = db.get_exam_questions(exam_id)
    question = next((q for q in rows if q["id"] == question_id), None)
    if question is None:
        raise ExamError("question_not_found", f"No question '{question_id}' in exam '{exam_id}'.")

    answer = (user_answer or "").strip() or "（未作答）"
    db.answer_exam_question(question_id, user_answer=answer, score=None)

    return {
        "exam_id": exam["id"],
        "question_id": question_id,
        "user_answer": answer,
        "recorded": True,
    }


def finish_exam(
    exam_id: str,
    user_id: str = "default",
    question_meta: Optional[dict[str, dict[str, Any]]] = None,
) -> dict:
    """
    Finalize the exam: reload every persisted question row, merge the
    client-carried qualitative error metadata, and delegate to
    examiner._finalize_exam.

    The total score is computed by _finalize_exam from the DB-persisted
    per-question scores (tamper-resistant). question_meta only supplies the
    qualitative error metadata (error_type / explanation / related_concepts);
    missing entries default to "incomplete" / "" / [].

    Returns the finalization summary. Raises exam_not_found for an unknown exam.
    """
    exam = _get_exam(exam_id, user_id)
    node = db.get_node(exam["node_id"])
    if not node:
        raise ExamError("node_not_found", f"Node not found for exam: {exam['node_id']}")

    meta = question_meta or {}
    rows = db.get_exam_questions(exam_id)

    scored_questions: list[dict] = []
    for row in rows:
        enriched = dict(row)
        qmeta = meta.get(row["id"], {}) if isinstance(meta, dict) else {}
        enriched["_error_type"] = qmeta.get("error_type") or "incomplete"
        enriched["_explanation"] = qmeta.get("explanation", "") or ""
        enriched["_related_concepts"] = qmeta.get("related_concepts", []) or []
        scored_questions.append(enriched)

    return _finalize_exam(exam_id, node, scored_questions, user_id=user_id)
