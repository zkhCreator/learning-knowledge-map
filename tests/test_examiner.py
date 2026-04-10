"""
tests/test_examiner.py

Unit tests for src/agents/examiner.py.
All LLM calls (llm.call_json) are mocked — no real API calls.
"""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


# ── Helpers ────────────────────────────────────────────────────────────────────

def _question(q="What is X?", answer="X is Y.", q_type="short_answer",
              source_section=1, is_expansion=False):
    return {
        "question": q,
        "expected_answer": answer,
        "question_type": q_type,
        "options": None,
        "source_section": source_section,
        "is_expansion": is_expansion,
    }


def _questions_response(n=3):
    return {"questions": [_question(f"Q{i}?", f"A{i}.") for i in range(1, n + 1)]}


def _score_response(score=0.9, error_type=None, explanation="Well done.", related=None):
    return {
        "score": score,
        "error_type": error_type,
        "explanation": explanation,
        "related_concepts": related or [],
    }


# ── generate_questions ────────────────────────────────────────────────────────

class TestGenerateQuestions:
    def _node(self, strictness="standard"):
        return {
            "id": "fake-id",
            "title": "Test Node",
            "description": "A test node.",
            "strictness_level": strictness,
        }

    @patch("src.agents.examiner.llm.call_json")
    def test_normal_returns_questions(self, mock_call):
        mock_call.return_value = _questions_response(3)
        from src.agents.examiner import generate_questions
        questions = generate_questions(self._node(), [], model=None)
        assert len(questions) == 3
        assert questions[0]["question"] == "Q1?"

    @patch("src.agents.examiner.llm.call_json")
    def test_list_response_normalised(self, mock_call):
        # Some models return a raw list
        mock_call.return_value = [_question("Q1?"), _question("Q2?")]
        from src.agents.examiner import generate_questions
        questions = generate_questions(self._node(), [])
        assert len(questions) == 2

    @patch("src.agents.examiner.llm.call_json")
    def test_empty_response_raises(self, mock_call):
        mock_call.return_value = {"questions": []}
        from src.agents.examiner import generate_questions
        with pytest.raises(ValueError, match="empty"):
            generate_questions(self._node(), [])

    @patch("src.agents.examiner.llm.call_json")
    def test_critical_node_hint_in_prompt(self, mock_call):
        mock_call.return_value = _questions_response(2)
        from src.agents.examiner import generate_questions
        generate_questions(self._node(strictness="critical"), [])
        prompt = mock_call.call_args[0][1]
        assert "critical" in prompt.lower() or "陷阱" in prompt

    @patch("src.agents.examiner.llm.call_json")
    def test_default_fields_normalised(self, mock_call):
        """Missing optional fields get defaults."""
        mock_call.return_value = {"questions": [{"question": "Q?", "expected_answer": "A."}]}
        from src.agents.examiner import generate_questions
        questions = generate_questions(self._node(), [])
        q = questions[0]
        assert q.get("question_type") == "short_answer"
        assert q.get("is_expansion") is False


# ── score_answer ──────────────────────────────────────────────────────────────

class TestScoreAnswer:
    @patch("src.agents.examiner.llm.call_json")
    def test_high_score_no_error_type(self, mock_call):
        mock_call.return_value = _score_response(score=0.95, error_type=None)
        from src.agents.examiner import score_answer
        result = score_answer("Q?", "A.", "My answer")
        assert result["score"] == pytest.approx(0.95)
        assert result["error_type"] is None

    @patch("src.agents.examiner.llm.call_json")
    def test_low_score_has_error_type(self, mock_call):
        mock_call.return_value = _score_response(
            score=0.3,
            error_type="memory_confusion",
            explanation="You confused X with Y.",
        )
        from src.agents.examiner import score_answer
        result = score_answer("Q?", "A.", "Wrong answer")
        assert result["score"] == pytest.approx(0.3)
        assert result["error_type"] == "memory_confusion"
        assert "confused" in result["explanation"]

    @patch("src.agents.examiner.llm.call_json")
    def test_api_failure_returns_default_zero(self, mock_call):
        mock_call.side_effect = RuntimeError("API timeout")
        from src.agents.examiner import score_answer
        result = score_answer("Q?", "A.", "answer")
        assert result["score"] == 0.0
        assert result["error_type"] == "incomplete"

    @patch("src.agents.examiner.llm.call_json")
    def test_non_dict_response_returns_zero(self, mock_call):
        mock_call.return_value = "not a dict"
        from src.agents.examiner import score_answer
        result = score_answer("Q?", "A.", "answer")
        assert result["score"] == 0.0

    @patch("src.agents.examiner.llm.call_json")
    def test_related_concepts_returned(self, mock_call):
        mock_call.return_value = _score_response(
            score=0.4, related=["Topic A", "Topic B"]
        )
        from src.agents.examiner import score_answer
        result = score_answer("Q?", "A.", "partial")
        assert "Topic A" in result["related_concepts"]


# ── _finalize_exam ────────────────────────────────────────────────────────────

class TestFinalizeExam:
    def _setup(self, tmp_db, make_node, score_override=None):
        """Create node, exam, and 3 answered question rows."""
        from src.db import database as db
        node = make_node(strictness_level="standard", mastery_threshold=0.80)
        exam = db.create_exam(node["id"])
        scored = []
        for i in range(1, 4):
            q = db.add_exam_question(
                exam["id"],
                question=f"Q{i}?",
                expected_answer=f"A{i}.",
                source_section=i,
            )
            s = score_override if score_override is not None else 0.9
            db.answer_exam_question(q["id"], user_answer=f"Ans{i}", score=s)
            enriched = dict(q)
            enriched["score"] = s
            enriched["user_answer"] = f"Ans{i}"
            enriched["_error_type"] = None if s >= 0.6 else "incomplete"
            enriched["_explanation"] = ""
            enriched["_related_concepts"] = []
            scored.append(enriched)
        return node, exam, scored

    def test_pass_sets_state_mastered(self, tmp_db, make_node):
        from src.db import database as db
        from src.agents.examiner import _finalize_exam
        node, exam, scored = self._setup(tmp_db, make_node, score_override=0.9)
        summary = _finalize_exam(exam["id"], node, scored)

        assert summary["passed"] is True
        state = db.get_state(node["id"])
        assert state["status"] == "mastered"
        assert state["raw_score"] == pytest.approx(0.9)

    def test_pass_creates_review_schedule(self, tmp_db, make_node):
        from src.db import database as db
        from src.agents.examiner import _finalize_exam
        node, exam, scored = self._setup(tmp_db, make_node, score_override=0.9)
        _finalize_exam(exam["id"], node, scored)

        due = db.get_due_reviews()
        # Scheduled in the future, so not "due" today — check it exists
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM review_schedule WHERE node_id=?", (node["id"],)
            ).fetchone()
        assert row is not None
        assert row["review_round"] == 1

    def test_fail_sets_state_learning(self, tmp_db, make_node):
        from src.db import database as db
        from src.agents.examiner import _finalize_exam
        node, exam, scored = self._setup(tmp_db, make_node, score_override=0.4)
        summary = _finalize_exam(exam["id"], node, scored)

        assert summary["passed"] is False
        state = db.get_state(node["id"])
        assert state["status"] == "learning"

    def test_fail_does_not_create_review_schedule(self, tmp_db, make_node):
        from src.db import database as db
        from src.agents.examiner import _finalize_exam
        node, exam, scored = self._setup(tmp_db, make_node, score_override=0.4)
        _finalize_exam(exam["id"], node, scored)

        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM review_schedule WHERE node_id=?", (node["id"],)
            ).fetchone()
        assert row is None

    def test_wrong_answers_written_to_error_notebook(self, tmp_db, make_node):
        from src.db import database as db
        from src.agents.examiner import _finalize_exam
        # 2 correct (0.9), 1 wrong (0.2)
        node = make_node()
        exam = db.create_exam(node["id"])
        scored = []
        for i, score in enumerate([0.9, 0.9, 0.2], 1):
            q = db.add_exam_question(
                exam["id"], question=f"Q{i}?", expected_answer=f"A{i}.", source_section=i
            )
            db.answer_exam_question(q["id"], user_answer="ans", score=score)
            enriched = dict(q)
            enriched["score"] = score
            enriched["user_answer"] = "ans"
            enriched["_error_type"] = None if score >= 0.6 else "boundary_unclear"
            enriched["_explanation"] = "explanation"
            enriched["_related_concepts"] = []
            scored.append(enriched)

        _finalize_exam(exam["id"], node, scored)
        errors = db.list_errors(node_id=node["id"])
        assert len(errors) == 1
        assert errors[0]["error_type"] == "boundary_unclear"

    def test_empty_scored_questions_returns_defaults(self, tmp_db, make_node):
        from src.db import database as db
        from src.agents.examiner import _finalize_exam
        node = make_node()
        exam = db.create_exam(node["id"])
        summary = _finalize_exam(exam["id"], node, [])
        assert summary["total_score"] == 0.0
        assert summary["passed"] is False

    def test_total_score_is_average(self, tmp_db, make_node):
        from src.db import database as db
        from src.agents.examiner import _finalize_exam
        node, exam, scored = self._setup(tmp_db, make_node, score_override=0.6)
        # Override scores: 1.0, 0.8, 0.6 → avg = 0.8
        scored[0]["score"] = 1.0
        scored[1]["score"] = 0.8
        scored[2]["score"] = 0.6
        # Update DB to match
        for q in scored:
            db.answer_exam_question(q["id"], user_answer="ans", score=q["score"])

        summary = _finalize_exam(exam["id"], node, scored)
        assert summary["total_score"] == pytest.approx(0.8)

    def test_exam_marked_finished_in_db(self, tmp_db, make_node):
        from src.db import database as db
        from src.agents.examiner import _finalize_exam
        node, exam, scored = self._setup(tmp_db, make_node, score_override=0.85)
        _finalize_exam(exam["id"], node, scored)

        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM exam_attempts WHERE id=?", (exam["id"],)
            ).fetchone()
        assert row["finished_at"] is not None
        assert row["total_score"] == pytest.approx(0.85)

    def test_stability_updated_after_pass(self, tmp_db, make_node):
        from src.db import database as db
        from src.agents.examiner import _finalize_exam
        node, exam, scored = self._setup(tmp_db, make_node, score_override=0.9)
        _finalize_exam(exam["id"], node, scored)

        state = db.get_state(node["id"])
        # Initial stability=1.0, after pass should be 1.0 * 1.5 = 1.5
        assert state["stability"] == pytest.approx(1.5)

    def test_stability_decreases_after_fail(self, tmp_db, make_node):
        from src.db import database as db
        from src.agents.examiner import _finalize_exam
        node, exam, scored = self._setup(tmp_db, make_node, score_override=0.3)
        _finalize_exam(exam["id"], node, scored)

        state = db.get_state(node["id"])
        # Initial stability=1.0, after fail should be 1.0 * 0.7 = 0.7
        assert state["stability"] == pytest.approx(0.7)

    def test_critical_node_uses_higher_threshold(self, tmp_db, make_node):
        from src.db import database as db
        from src.agents.examiner import _finalize_exam
        # critical threshold = 0.95
        node = make_node(strictness_level="critical", mastery_threshold=0.95)
        exam = db.create_exam(node["id"])
        # score = 0.85 → passes standard (0.80) but fails critical (0.95)
        q = db.add_exam_question(exam["id"], question="Q?", expected_answer="A.")
        db.answer_exam_question(q["id"], user_answer="ans", score=0.85)
        scored = [dict(q)]
        scored[0].update({"score": 0.85, "user_answer": "ans", "_error_type": None,
                          "_explanation": "", "_related_concepts": []})
        summary = _finalize_exam(exam["id"], node, scored)
        assert summary["passed"] is False
