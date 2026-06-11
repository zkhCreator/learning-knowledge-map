"""
File: src/services/review.py

Purpose:
    Stateless, non-interactive review services behind the GUI workflow host.
    This is the HTTP-facing decomposition of agents.reviewer.run_review_loop,
    split into independent steps so the browser can browse the review queue and
    run a scheduled review without the terminal REPL.

Responsibilities:
    - get_queue: build the Ebbinghaus review queue and group it for display
      (critical / overdue / today / future), reusing reviewer.get_review_queue's
      priority sort. Read-only.
    - start_review: assemble the review context for a node (historical errors +
      mnemonic retrieval) so the user can confirm before the re-exam. Does not
      create the exam — the re-exam reuses the Exam primitives (the GUI calls the
      Exam API for the question loop), keeping Review's entry independent.
    - finish_review: finalize a review by delegating the exam result to the Exam
      service (which writes state, the error notebook, and the next review on
      pass), then completing the OLD review record and, on a failed re-exam,
      rescheduling — mirroring run_review_loop's completion logic.

What this file does NOT do:
    - Hold server-side memory between calls. Durable review state lives in
      SQLite (review_schedule) and is reloaded per request.
    - Call input() / Rich / console output, or run run_review_loop over HTTP.
    - Add DB tables or schema changes.
    - Reimplement queue priority, exam scoring, or Ebbinghaus math — it reuses
      agents.reviewer, the exam service, and graph.dag.

Key Design Decisions:
    - Each pending review lands in exactly one display group: a critical-
      strictness review goes to "critical" (its own urgency tier, matching
      reviewer._priority); otherwise it is grouped by scheduled date relative to
      today (overdue < today < future).
    - include_future lets the queue hide not-yet-due reviews; the reported total
      counts only the groups actually returned.
    - Every public function takes an explicit user_id (doc/14 rule).

Inputs:
    - user_id: learner id; include_future: whether to keep the future group

Outputs:
    - get_queue -> {groups:{critical,overdue,today,future}, counts, total}
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.agents.mnemonic import get_retrieval_context
from src.agents.reviewer import get_review_queue
from src.data import database as db
from src.domain import dag as dag_utils
from src.infrastructure.logger import get_logger

log = get_logger(__name__)


class ReviewError(ValueError):
    """Raised for expected review errors that should become JSON envelopes."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _review_view(row: dict) -> dict:
    """Client-safe view of a review_schedule row joined with node info."""
    return {
        "review_id": row.get("id"),
        "node_id": row.get("node_id"),
        "node_title": row.get("node_title", row.get("node_id")),
        "strictness_level": row.get("strictness_level", "standard"),
        "mastery_threshold": row.get("mastery_threshold"),
        "scheduled_at": row.get("scheduled_at"),
        "review_round": row.get("review_round", 1),
        "status": row.get("status", "pending"),
    }


def _date_of(iso_str: str | None) -> "datetime.date | None":
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str).date()
    except ValueError:
        # tolerate a bare date or trailing 'Z'
        try:
            return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def get_queue(user_id: str = "default", include_future: bool = True) -> dict:
    """
    Build the review queue grouped for display.

    Groups (each review in exactly one):
        - critical: critical-strictness pending reviews (top urgency tier)
        - overdue:  scheduled date before today
        - today:    scheduled date is today
        - future:   scheduled date after today (dropped when include_future=False)

    Returns {groups, counts, total}. total counts only the returned groups.
    """
    reviews = get_review_queue(user_id=user_id)  # already priority-sorted
    today = datetime.now(timezone.utc).date()

    groups: dict[str, list[dict]] = {
        "critical": [],
        "overdue": [],
        "today": [],
        "future": [],
    }

    for row in reviews:
        view = _review_view(row)
        if row.get("strictness_level") == "critical":
            groups["critical"].append(view)
            continue
        sched = _date_of(row.get("scheduled_at"))
        if sched is None or sched < today:
            groups["overdue"].append(view)
        elif sched == today:
            groups["today"].append(view)
        else:
            groups["future"].append(view)

    if not include_future:
        groups["future"] = []

    counts = {key: len(value) for key, value in groups.items()}
    total = sum(counts.values())
    return {"groups": groups, "counts": counts, "total": total}


# ── Review Start / Finish ──────────────────────────────────────────────────────

def _node_ctx(node: dict) -> dict:
    """Client-safe view of the node under review."""
    return {
        "id": node["id"],
        "title": node.get("title", node["id"]),
        "description": node.get("description", ""),
        "strictness_level": node.get("strictness_level", "standard"),
        "mastery_threshold": node.get("mastery_threshold", 0.80),
    }


def _error_view(row: dict) -> dict:
    """Client-safe view of an error_notebook row for review priming."""
    return {
        "question": row.get("question", ""),
        "error_type": row.get("error_type"),
        "user_answer": row.get("user_answer", ""),
        "correct_answer": row.get("correct_answer", ""),
        "explanation": row.get("explanation", ""),
        "source_section_title": row.get("source_section_title", ""),
    }


def _pending_review_by_id(review_id: str, user_id: str) -> dict | None:
    with db.get_connection() as conn:
        row = conn.execute(
            """SELECT r.*, n.title AS node_title, n.strictness_level,
                      n.mastery_threshold
               FROM review_schedule r
               JOIN knowledge_nodes n ON n.id = r.node_id
               WHERE r.id = ? AND r.user_id = ?
               LIMIT 1""",
            (review_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def _pending_review_for_node(node_id: str, user_id: str) -> dict | None:
    with db.get_connection() as conn:
        row = conn.execute(
            """SELECT r.*, n.title AS node_title, n.strictness_level,
                      n.mastery_threshold
               FROM review_schedule r
               JOIN knowledge_nodes n ON n.id = r.node_id
               WHERE r.user_id = ? AND r.node_id = ? AND r.status = 'pending'
               ORDER BY r.scheduled_at ASC LIMIT 1""",
            (user_id, node_id),
        ).fetchone()
    return dict(row) if row else None


def start_review(
    review_id: str | None = None,
    node: str | None = None,
    user_id: str = "default",
) -> dict:
    """
    Assemble the review context for a pending review or a manual node review.

    Provide a review_id (preferred) or a node id. Returns the node, the review
    round/schedule (null for a manual review with no pending schedule), the
    historical error notebook entries, and the mnemonic retrieval context (or
    None). Does NOT create the re-exam — the GUI reuses the Exam primitives for
    the question loop and then calls finish_review.
    """
    review: dict | None = None
    node_id: str | None = None
    review_round = 1
    scheduled_at = None

    if review_id:
        review = _pending_review_by_id(review_id, user_id)
        if not review:
            raise ReviewError("review_not_found", f"No review '{review_id}' for user.")
        node_id = review["node_id"]
        review_round = review.get("review_round", 1)
        scheduled_at = review.get("scheduled_at")
    elif node:
        node_row = db.get_node(node)
        if not node_row:
            raise ReviewError("node_not_found", f"No node '{node}'.")
        node_id = node_row["id"]
        review = _pending_review_for_node(node_id, user_id)
        if review:
            review_id = review["id"]
            review_round = review.get("review_round", 1)
            scheduled_at = review.get("scheduled_at")
    else:
        raise ReviewError("missing_target", "Provide a review id or a node id.")

    node_row = db.get_node(node_id)
    if not node_row:
        raise ReviewError("node_not_found", f"Node not found: {node_id}")

    errors = [_error_view(e) for e in db.list_errors(user_id=user_id, node_id=node_id)]

    mnemonic = None
    ctx = get_retrieval_context(node_id=node_id, user_id=user_id)
    if ctx:
        mnemonic = {
            "strategy": ctx.get("strategy"),
            "prompt": ctx.get("prompt", ""),
            "display": ctx.get("display", ""),
        }

    return {
        "review_id": review["id"] if review else None,
        "node": _node_ctx(node_row),
        "review_round": review_round,
        "scheduled_at": scheduled_at,
        "errors": errors,
        "mnemonic": mnemonic,
    }


def finish_review(
    exam_id: str,
    review_id: str | None = None,
    node: str | None = None,
    user_id: str = "default",
    question_meta: dict | None = None,
) -> dict:
    """
    Finalize a review whose re-exam was run through the Exam primitives.

    Delegates the exam result to the Exam service (which updates state, writes
    the error notebook, and schedules the next review on pass), then completes
    the OLD pending review record and — on a failed re-exam — reschedules so the
    node stays in rotation (mirroring run_review_loop). For a manual review with
    no review_id, only the exam finalization happens.
    """
    # Import lazily so DB_PATH is fixed before src.infrastructure.config (matches the adapter).
    from src.services.exam import ExamError, finish_exam as finish_exam_attempt

    try:
        summary = finish_exam_attempt(exam_id, user_id=user_id, question_meta=question_meta)
    except ExamError as exc:
        raise ReviewError(exc.code, exc.message) from exc

    passed = bool(summary.get("passed"))
    total_score = float(summary.get("total_score", 0.0))
    next_review_days = summary.get("interval_days")

    if review_id:
        review = _pending_review_by_id(review_id, user_id)
        if not review:
            raise ReviewError("review_not_found", f"No review '{review_id}' for user.")
        node_row = db.get_node(review["node_id"])
        threshold = node_row.get("mastery_threshold", 0.80) if node_row else 0.80
        next_round = review.get("review_round", 1) + 1
        interval_days = dag_utils.next_review_interval(next_round, total_score, threshold)
        db.complete_review(
            review_id=review_id,
            score=total_score,
            next_interval_days=interval_days,
        )
        if not passed:
            # _finalize_exam only schedules on pass; keep failed nodes in rotation.
            next_date = (datetime.now(timezone.utc) + timedelta(days=interval_days)).isoformat()
            db.create_review(
                node_id=review["node_id"],
                scheduled_at=next_date,
                review_round=next_round,
                user_id=user_id,
            )
            next_review_days = interval_days
        node_id = review["node_id"]
    else:
        node_id = node

    return {
        "review_id": review_id,
        "node_id": node_id,
        "passed": passed,
        "total_score": total_score,
        "threshold": summary.get("threshold"),
        "next_review_days": next_review_days,
        "weak_sections": summary.get("weak_sections", []),
    }
