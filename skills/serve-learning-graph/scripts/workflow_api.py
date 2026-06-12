"""
File: skills/serve-learning-graph/scripts/workflow_api.py

Purpose:
    Provide a JSON command adapter for the local GUI workflow host.

Responsibilities:
    - Resolve the explicit database path before project config can be imported
    - Dispatch small GUI API actions from Node to Python
    - Return stable JSON envelopes for browser-facing HTTP endpoints
    - Keep read-only graph and goal actions separate from future write workflows

What this file does NOT do:
    - Start an HTTP server
    - Render HTML
    - Call CLI REPL loops or read terminal input
    - Access SQLite from Node

Inputs: CLI action flags plus optional JSON payload on stdin
Outputs: JSON envelope on stdout
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from export_graph import export_graph, resolve_db_path

# Make the workflow service layer (src.*) importable when this adapter runs as
# a standalone subprocess from any cwd: vendored _core/ when installed, repo
# root in development — see docs/22 and scripts/_bootstrap.py. The lazy
# `from src.services...` imports inside the assessment handlers depend on this.
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
import _bootstrap  # noqa: E402,F401


FEATURES = ["graph", "goals", "assess", "learn", "exam", "review", "workflow-host"]


class WorkflowApiError(ValueError):
    """Raised for expected workflow API errors that should become JSON."""

    def __init__(self, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra or {}


# Generation steps the local GUI must hand back to Codex / Claude Code. Each
# entry maps a gated web action to the skill the user should run and the relative
# deep-link (?view=…) they return to once the skill has persisted its output.
def _agent_required(skill: str, command: str, deep_link: str) -> "WorkflowApiError":
    """Build the `agent_required` error that tells the GUI to defer to the tool."""
    return WorkflowApiError(
        "agent_required",
        f"该步骤需要大模型生成,请在 Codex / Claude Code 中运行 `{command}`,"
        f"完成后回到本页继续。",
        extra={"skill": skill, "command": command, "deep_link": deep_link},
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _read_payload(stdin_text: str) -> dict[str, Any]:
    if not stdin_text.strip():
        return {}
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError as exc:
        raise WorkflowApiError("invalid_json", f"Invalid JSON payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkflowApiError("invalid_payload", "JSON payload must be an object.")
    return payload


def _list_goals(db_path: Path, user_id: str) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT id, user_id, title, root_node, status, created_at
               FROM learning_goals
               WHERE user_id = ?
               ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def handle_action(
    action: str,
    db_path: str | Path | None = None,
    user_id: str = "default",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_db = resolve_db_path(db_path)
    os.environ["DB_PATH"] = str(resolved_db)
    body = payload or {}
    effective_user = str(body.get("user_id") or body.get("user") or user_id)

    if action == "health":
        return {
            "db_path": str(resolved_db),
            "user_id": effective_user,
            "features": FEATURES,
        }

    if action == "goals":
        return {
            "db_path": str(resolved_db),
            "user_id": effective_user,
            "goals": _list_goals(resolved_db, effective_user),
        }

    if action == "graph":
        goal = body.get("goal") or body.get("goal_id") or body.get("goal_prefix")
        graph = export_graph(
            db_path=resolved_db,
            goal_prefix=str(goal) if goal else None,
            user_id=effective_user,
        )
        return {"graph": graph}

    if action == "assessment_start":
        return _assessment_start(body, effective_user)

    if action == "assessment_answer":
        return _assessment_answer(body, effective_user)

    if action == "learn_prepare":
        return _learn_prepare(body, effective_user)

    if action == "learn_message":
        return _learn_message(body, effective_user)

    if action == "exam_start":
        return _exam_start(body, effective_user)

    if action == "exam_get":
        return _exam_get(body, effective_user)

    if action == "exam_record_answer":
        return _exam_record_answer(body, effective_user)

    if action == "exam_answer":
        return _exam_answer(body, effective_user)

    if action == "exam_finish":
        return _exam_finish(body, effective_user)

    if action == "review_queue":
        return _review_queue(body, effective_user)

    if action == "review_start":
        return _review_start(body, effective_user)

    if action == "review_finish":
        return _review_finish(body, effective_user)

    raise WorkflowApiError("unknown_action", f"Unknown workflow action: {action}")


def _require_goal(body: dict[str, Any]) -> str:
    goal = body.get("goal") or body.get("goal_id") or body.get("goal_prefix")
    if not goal:
        raise WorkflowApiError("goal_not_found", "Missing goal id or prefix.")
    return str(goal)


def _assessment_start(body: dict[str, Any], user_id: str) -> dict[str, Any]:
    # Generation step: probing/scoring needs the LLM. Hand it to the tool.
    goal = _require_goal(body)
    raise _agent_required("goal-assess", f"/goal-assess {goal}", f"?view=assess&goal={goal}")


def _assessment_answer(body: dict[str, Any], user_id: str) -> dict[str, Any]:
    goal = _require_goal(body)
    raise _agent_required("goal-assess", f"/goal-assess {goal}", f"?view=assess&goal={goal}")


def _require_node(body: dict[str, Any]) -> str:
    node = body.get("node") or body.get("node_id") or body.get("node_prefix")
    if not node:
        raise WorkflowApiError("node_not_found", "Missing node id or prefix.")
    return str(node)


def _learn_prepare(body: dict[str, Any], user_id: str) -> dict[str, Any]:
    # Generation step: outline + Socratic turns need the LLM. Hand it to the tool.
    node = _require_node(body)
    raise _agent_required("learn-start", f"/learn-start {node}", f"?view=learn&node={node}")


def _learn_message(body: dict[str, Any], user_id: str) -> dict[str, Any]:
    session_id = body.get("session_id") or body.get("session")
    if not session_id:
        raise WorkflowApiError("session_not_found", "Missing session id.")
    raise _agent_required(
        "learn-start", "/learn-start <node>", f"?view=learn&session={session_id}"
    )


def _exam_start(body: dict[str, Any], user_id: str) -> dict[str, Any]:
    # Generation step: question generation needs the LLM. Hand it to the tool.
    node = _require_node(body)
    raise _agent_required("exam-start", f"/exam-start {node}", f"?view=exam&node={node}")


def _require_exam(body: dict[str, Any]) -> str:
    exam_id = body.get("exam_id") or body.get("exam")
    if not exam_id:
        raise WorkflowApiError("exam_not_found", "Missing exam id.")
    return str(exam_id)


def _exam_get(body: dict[str, Any], user_id: str) -> dict[str, Any]:
    # Pure data: load a tool-generated exam for display (no expected_answer).
    from src.services.exam import ExamError, get_exam_view

    exam_id = _require_exam(body)
    try:
        return get_exam_view(exam_id=exam_id, user_id=user_id)
    except ExamError as exc:
        raise WorkflowApiError(exc.code, exc.message) from exc


def _exam_record_answer(body: dict[str, Any], user_id: str) -> dict[str, Any]:
    # Pure data: persist the learner's raw answer. Scoring happens in the tool.
    from src.services.exam import ExamError, record_answer

    exam_id = _require_exam(body)
    question_id = body.get("question_id") or body.get("question")
    if not question_id:
        raise WorkflowApiError("question_not_found", "Missing question id.")
    try:
        return record_answer(
            exam_id=exam_id,
            question_id=str(question_id),
            user_id=user_id,
            user_answer=body.get("user_answer", ""),
        )
    except ExamError as exc:
        raise WorkflowApiError(exc.code, exc.message) from exc


def _exam_answer(body: dict[str, Any], user_id: str) -> dict[str, Any]:
    # Scoring step needs the LLM. Hand it to the tool's exam scoring flow.
    exam_id = _require_exam(body)
    raise _agent_required(
        "exam-start", f"/exam-start --score {exam_id}", f"?view=exam&exam={exam_id}"
    )


def _exam_finish(body: dict[str, Any], user_id: str) -> dict[str, Any]:
    # Finalization aggregates LLM-produced scores; it is part of the tool flow.
    exam_id = _require_exam(body)
    raise _agent_required(
        "exam-start", f"/exam-start --score {exam_id}", f"?view=exam&exam={exam_id}"
    )


def _review_queue(body: dict[str, Any], user_id: str) -> dict[str, Any]:
    # Import lazily so DB_PATH (set in handle_action) is fixed before src.infrastructure.config.
    from src.services.review import get_queue

    include_future = body.get("include_future")
    if isinstance(include_future, str):
        include_future = include_future.lower() not in ("false", "0", "no", "")
    elif include_future is None:
        include_future = True
    return get_queue(user_id=user_id, include_future=bool(include_future))


def _review_start(body: dict[str, Any], user_id: str) -> dict[str, Any]:
    from src.services.review import ReviewError, start_review

    review_id = body.get("review_id") or body.get("review")
    node = body.get("node") or body.get("node_id")
    if not review_id and not node:
        raise WorkflowApiError("missing_target", "Provide a review id or a node id.")
    try:
        return start_review(
            review_id=str(review_id) if review_id else None,
            node=str(node) if node else None,
            user_id=user_id,
        )
    except ReviewError as exc:
        raise WorkflowApiError(exc.code, exc.message) from exc


def _review_finish(body: dict[str, Any], user_id: str) -> dict[str, Any]:
    # The review re-exam (generation + scoring + finalize) runs in the tool.
    node = body.get("node") or body.get("node_id")
    command = f"/review-start {node}" if node else "/review-start <node>"
    deep_link = f"?view=review&node={node}" if node else "?view=review"
    raise _agent_required("review-start", command, deep_link)


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _error(code: str, message: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if extra:
        error.update(extra)
    return {"ok": False, "error": error}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a GUI workflow API action.")
    parser.add_argument("--db", default=None, help="SQLite database path")
    parser.add_argument("--action", required=True, help="Action name")
    parser.add_argument("--user", default="default", help="User ID")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        resolved_db = resolve_db_path(args.db)
        os.environ["DB_PATH"] = str(resolved_db)
        payload = _read_payload(sys.stdin.read())
        result = handle_action(
            action=args.action,
            db_path=resolved_db,
            user_id=args.user,
            payload=payload,
        )
        print(json.dumps(_ok(result), ensure_ascii=False))
    except WorkflowApiError as exc:
        print(json.dumps(_error(exc.code, exc.message, exc.extra), ensure_ascii=False))
    except Exception as exc:  # pragma: no cover - exercised through subprocess paths
        print(json.dumps(_error("internal_error", str(exc)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
