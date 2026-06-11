"""
File: skills/exam-start/scripts/exam_cli.py

Purpose:
    Run the LLM-backed exam steps that the GUI data plane defers to the tool:
    generate a question set, and (after the learner records answers in the web
    host) score those answers and finalize the attempt.

Responsibilities:
    - generate: create + persist an exam for a node, then print a deep link to
      the web host so the learner can answer it (?view=exam&exam=<id>).
    - score: score every recorded raw answer, finalize the attempt (mastery
      state, error notebook, review schedule), then print a result deep link.

What this file does NOT do:
    - Run inside the web host (that process is LLM-free by design)
    - Render UI or record answers (the learner does that in the browser)
    - Add DB tables (reuses src/services/exam.py + src/agents/examiner.py)

Inputs:  subcommand (generate | score) + db path, node/exam id, user id
Outputs: human-readable status on stdout, ending with a clickable deep link
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _set_db_path(db: str | None) -> str:
    # Fix DB_PATH before importing src.infrastructure.config (which reads it at import time).
    resolved = str(Path(db).resolve()) if db else str(_PROJECT_ROOT / "data" / "learning.db")
    os.environ["DB_PATH"] = resolved
    return resolved


def _generate(node: str, user: str) -> int:
    from src.services.exam import start_exam
    from src.infrastructure.web_link import deep_link

    data = start_exam(node=node, user_id=user)
    print(f"Generated exam {data['exam_id']} for node {data['node']['title']}")
    print(f"Questions: {data['total']}")
    print("Answer it here, then come back to score:")
    print(deep_link("exam", exam=data["exam_id"]))
    return 0


def _score(exam_id: str, user: str) -> int:
    from src.agents.examiner import score_answer
    from src.data import database as db
    from src.services.exam import _get_exam, finish_exam  # noqa: WPS437 (intentional reuse)
    from src.infrastructure.web_link import deep_link

    exam = _get_exam(exam_id, user)
    node = db.get_node(exam["node_id"])
    if not node:
        print(f"error: node not found for exam {exam_id}", file=sys.stderr)
        return 1

    strictness = node.get("strictness_level", "standard")
    question_meta: dict[str, dict] = {}
    for row in db.get_exam_questions(exam_id):
        answer = row.get("user_answer") or "（未作答）"
        scoring = score_answer(
            question=row["question"],
            expected_answer=row["expected_answer"],
            user_answer=answer,
            strictness=strictness,
        )
        db.answer_exam_question(row["id"], user_answer=answer, score=float(scoring["score"]))
        question_meta[row["id"]] = {
            "error_type": scoring.get("error_type"),
            "explanation": scoring.get("explanation", ""),
            "related_concepts": scoring.get("related_concepts", []),
        }

    summary = finish_exam(exam_id=exam_id, user_id=user, question_meta=question_meta)
    verdict = "PASSED" if summary["passed"] else "FAILED"
    print(f"Exam {exam_id}: {verdict} — {round(summary['total_score'] * 100)}% "
          f"(threshold {round(summary['threshold'] * 100)}%)")
    if summary.get("weak_sections"):
        print("Weak sections: " + "、".join(summary["weak_sections"]))
    if summary["passed"] and summary.get("next_review"):
        print(f"Next review: {summary['next_review']}")
    print("Review the result here:")
    print(deep_link("exam", exam=exam_id))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or score an exam (LLM-backed).")
    parser.add_argument("command", choices=["generate", "score"])
    parser.add_argument("--db", default=None, help="SQLite database path")
    parser.add_argument("--user", default="default")
    parser.add_argument("--node", default=None, help="Node id/prefix (generate)")
    parser.add_argument("--exam", default=None, help="Exam id (score)")
    args = parser.parse_args(argv)

    _set_db_path(args.db)

    if args.command == "generate":
        if not args.node:
            parser.error("generate requires --node")
        return _generate(args.node, args.user)

    if not args.exam:
        parser.error("score requires --exam")
    return _score(args.exam, args.user)


if __name__ == "__main__":
    raise SystemExit(main())
