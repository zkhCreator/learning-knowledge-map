"""
tests/test_exam_service.py

Unit tests for src/services/exam.py (stateless exam-start service).

The service is the HTTP-facing decomposition of agents.examiner.run_exam_loop:
it exposes question generation/persistence, per-answer scoring, and exam
finalization as pure, non-interactive request/response steps. It does NOT
expose the blocking REPL.

All LLM-backed work is mocked — no real API calls. We patch the names as the
service imports them: src.services.exam.generate_questions and
src.services.exam.score_answer. _finalize_exam is the real examiner function so
the durable writes (state + review schedule + error notebook) are exercised.

TDD: tests written before implementation.
"""

import pytest
from unittest.mock import patch


# ── Helpers ────────────────────────────────────────────────────────────────────

def _questions(n=2):
    """Two generated questions: §1 (main) and an expansion."""
    return [
        {
            "question_type": "short_answer",
            "question": f"Question {i + 1}?",
            "options": None,
            "expected_answer": f"Expected answer {i + 1}.",
            "source_section": (i + 1) if i == 0 else None,
            "is_expansion": i != 0,
        }
        for i in range(n)
    ]


def _score(score, error_type=None, explanation="ok", related=None):
    return {
        "score": score,
        "error_type": error_type,
        "explanation": explanation,
        "related_concepts": related or [],
    }


def _answer_all(exam_id, node_id, user_id="default", scores=None):
    """Drive start -> answer every question, returning the start data."""
    from src.services.exam import answer_question

    from src.data import database as db
    rows = db.get_exam_questions(exam_id)
    scores = scores or [1.0] * len(rows)
    meta = {}
    for row, s in zip(rows, scores):
        with patch("src.services.exam.score_answer", return_value=_score(
            s, error_type=None if s >= 0.6 else "incomplete",
            explanation=f"exp-{row['id']}", related=["RelatedConcept"] if s < 0.6 else [],
        )):
            res = answer_question(
                exam_id=exam_id,
                question_id=row["id"],
                user_id=user_id,
                user_answer="my answer",
            )
        meta[res["question_id"]] = {
            "error_type": res["error_type"],
            "explanation": res["explanation"],
            "related_concepts": res["related_concepts"],
        }
    return meta


# ── start_exam ──────────────────────────────────────────────────────────────────

class TestStartExam:
    def test_unknown_node_raises(self, tmp_db):
        from src.services.exam import start_exam, ExamError
        with pytest.raises(ExamError) as exc:
            start_exam(node="nope", user_id="default")
        assert exc.value.code == "node_not_found"

    def test_ambiguous_node_raises(self, tmp_db, make_node):
        from src.services.exam import start_exam, ExamError
        # Two nodes; resolve with empty prefix matches >1 via LIKE.
        make_node(title="A")
        make_node(title="B")
        with patch("src.services.exam.generate_questions", return_value=_questions()):
            with pytest.raises(ExamError) as exc:
                start_exam(node="", user_id="default")
        assert exc.value.code == "node_not_found"

    def test_start_persists_questions_without_leaking_expected_answer(
        self, tmp_db, make_node, make_outline
    ):
        from src.services.exam import start_exam
        node = make_node(title="Photosynthesis")
        make_outline(node_id=node["id"])

        with patch("src.services.exam.generate_questions", return_value=_questions()) as gen:
            data = start_exam(node=node["id"], user_id="default")

        assert gen.called
        assert data["total"] == 2
        assert data["node"]["id"] == node["id"]
        assert "mastery_threshold" in data["node"]
        # No expected_answer leaked to the client.
        for q in data["questions"]:
            assert "expected_answer" not in q
            assert "id" in q and "index" in q and "question" in q

        # But it IS persisted server-side for scoring.
        from src.data import database as db
        rows = db.get_exam_questions(data["exam_id"])
        assert len(rows) == 2
        assert all(r["expected_answer"] for r in rows)

    def test_start_without_outline_falls_back_to_empty_sections(self, tmp_db, make_node):
        from src.services.exam import start_exam
        node = make_node(title="No Outline Node")

        captured = {}

        def _gen(node_arg, sections, model=None):
            captured["sections"] = sections
            return _questions(1)

        with patch("src.services.exam.generate_questions", side_effect=_gen):
            with patch("src.services.exam.generate_outline", side_effect=RuntimeError("boom")):
                data = start_exam(node=node["id"], user_id="default")

        assert captured["sections"] == []
        assert data["total"] == 1


# ── answer_question ─────────────────────────────────────────────────────────────

class TestAnswerQuestion:
    def test_persists_score_and_returns_metadata(self, tmp_db, make_node, make_outline):
        from src.services.exam import start_exam, answer_question
        node = make_node()
        make_outline(node_id=node["id"])
        with patch("src.services.exam.generate_questions", return_value=_questions()):
            data = start_exam(node=node["id"], user_id="default")

        from src.data import database as db
        q = db.get_exam_questions(data["exam_id"])[0]

        with patch("src.services.exam.score_answer", return_value=_score(0.4, "incomplete", "missed key point", ["X"])) as sc:
            res = answer_question(
                exam_id=data["exam_id"],
                question_id=q["id"],
                user_id="default",
                user_answer="partial",
            )

        # Scored using the DB-authoritative question + expected_answer + node strictness.
        kwargs = sc.call_args.kwargs
        assert kwargs["question"] == q["question"]
        assert kwargs["expected_answer"] == q["expected_answer"]
        assert kwargs["strictness"] == node["strictness_level"]

        assert res["question_id"] == q["id"]
        assert res["score"] == 0.4
        assert res["error_type"] == "incomplete"
        assert res["related_concepts"] == ["X"]

        # Persisted.
        row = db.get_exam_questions(data["exam_id"])[0]
        assert row["score"] == 0.4
        assert row["user_answer"] == "partial"

    def test_empty_answer_treated_as_unanswered(self, tmp_db, make_node, make_outline):
        from src.services.exam import start_exam, answer_question
        node = make_node()
        make_outline(node_id=node["id"])
        with patch("src.services.exam.generate_questions", return_value=_questions()):
            data = start_exam(node=node["id"], user_id="default")
        from src.data import database as db
        q = db.get_exam_questions(data["exam_id"])[0]

        with patch("src.services.exam.score_answer", return_value=_score(0.0, "incomplete")) as sc:
            answer_question(
                exam_id=data["exam_id"], question_id=q["id"],
                user_id="default", user_answer="   ",
            )
        assert sc.call_args.kwargs["user_answer"] == "（未作答）"
        row = db.get_exam_questions(data["exam_id"])[0]
        assert row["user_answer"] == "（未作答）"

    def test_unknown_exam_raises(self, tmp_db):
        from src.services.exam import answer_question, ExamError
        with pytest.raises(ExamError) as exc:
            answer_question(exam_id="nope", question_id="q", user_id="default", user_answer="x")
        assert exc.value.code == "exam_not_found"

    def test_unknown_question_raises(self, tmp_db, make_node, make_outline):
        from src.services.exam import start_exam, answer_question, ExamError
        node = make_node()
        make_outline(node_id=node["id"])
        with patch("src.services.exam.generate_questions", return_value=_questions()):
            data = start_exam(node=node["id"], user_id="default")
        with pytest.raises(ExamError) as exc:
            answer_question(
                exam_id=data["exam_id"], question_id="nope",
                user_id="default", user_answer="x",
            )
        assert exc.value.code == "question_not_found"


# ── finish_exam ─────────────────────────────────────────────────────────────────

class TestFinishExam:
    def test_pass_computes_total_from_db_and_creates_review_row(
        self, tmp_db, make_node, make_outline
    ):
        from src.services.exam import start_exam, finish_exam
        node = make_node(mastery_threshold=0.80)
        make_outline(node_id=node["id"])
        with patch("src.services.exam.generate_questions", return_value=_questions()):
            data = start_exam(node=node["id"], user_id="default")

        meta = _answer_all(data["exam_id"], node["id"], scores=[1.0, 0.9])

        summary = finish_exam(
            exam_id=data["exam_id"], user_id="default", question_meta=meta,
        )

        # Total computed from DB-persisted scores: (1.0 + 0.9) / 2 = 0.95.
        assert summary["total_score"] == pytest.approx(0.95)
        assert summary["passed"] is True
        assert summary["threshold"] == 0.80

        from src.data import database as db
        # State set to mastered.
        state = db.get_state(node["id"], "default")
        assert state["status"] == "mastered"

        # A review_schedule row IS created on pass (Modules 6/7 depend on it).
        with db.get_connection() as conn:
            reviews = conn.execute(
                "SELECT * FROM review_schedule WHERE node_id=? AND user_id=?",
                (node["id"], "default"),
            ).fetchall()
        assert len(reviews) == 1
        assert reviews[0]["status"] == "pending"
        assert summary["next_review"] is not None

    def test_fail_sets_learning_no_review_writes_error_notebook(
        self, tmp_db, make_node, make_outline
    ):
        from src.services.exam import start_exam, finish_exam
        node = make_node(mastery_threshold=0.80)
        make_outline(node_id=node["id"])
        with patch("src.services.exam.generate_questions", return_value=_questions()):
            data = start_exam(node=node["id"], user_id="default")

        # Both weak (< 0.6): merged client metadata must land in error notebook.
        meta = _answer_all(data["exam_id"], node["id"], scores=[0.2, 0.3])

        summary = finish_exam(
            exam_id=data["exam_id"], user_id="default", question_meta=meta,
        )
        assert summary["passed"] is False

        from src.data import database as db
        state = db.get_state(node["id"], "default")
        assert state["status"] == "learning"

        # No review row on fail.
        with db.get_connection() as conn:
            reviews = conn.execute(
                "SELECT * FROM review_schedule WHERE node_id=?", (node["id"],),
            ).fetchall()
        assert len(reviews) == 0

        # Error notebook has entries for both weak questions, carrying client metadata.
        errors = db.list_errors(user_id="default")
        assert len(errors) == 2
        assert all(e["error_type"] == "incomplete" for e in errors)
        # Explanation merged from client metadata.
        assert all(e["explanation"].startswith("exp-") for e in errors)

    def test_finish_with_no_questions(self, tmp_db, make_node):
        from src.services.exam import finish_exam
        from src.data import database as db
        node = make_node()
        exam = db.create_exam(node_id=node["id"], user_id="default")
        summary = finish_exam(exam_id=exam["id"], user_id="default")
        assert summary["passed"] is False
        assert summary["total_score"] == 0.0

    def test_missing_question_meta_defaults_to_incomplete(self, tmp_db, make_node, make_outline):
        from src.services.exam import start_exam, finish_exam
        node = make_node(mastery_threshold=0.80)
        make_outline(node_id=node["id"])
        with patch("src.services.exam.generate_questions", return_value=_questions()):
            data = start_exam(node=node["id"], user_id="default")
        # Answer weak but pass NO question_meta to finish.
        _answer_all(data["exam_id"], node["id"], scores=[0.2, 0.2])
        summary = finish_exam(exam_id=data["exam_id"], user_id="default", question_meta=None)
        assert summary["passed"] is False
        from src.data import database as db
        errors = db.list_errors(user_id="default")
        assert len(errors) == 2
        assert all(e["error_type"] == "incomplete" for e in errors)

    def test_unknown_exam_raises(self, tmp_db):
        from src.services.exam import finish_exam, ExamError
        with pytest.raises(ExamError) as exc:
            finish_exam(exam_id="nope", user_id="default")
        assert exc.value.code == "exam_not_found"


# ── GUI data plane: get_exam_view + record_answer (no LLM) ───────────────────────

class TestExamDataPlane:
    """The web host loads a tool-generated exam and records raw answers without
    ever calling the LLM (scoring is deferred to the tool)."""

    def _make_exam(self, make_node, make_outline):
        from src.services.exam import start_exam
        node = make_node(title="Cells")
        make_outline(node_id=node["id"])
        with patch("src.services.exam.generate_questions", return_value=_questions()):
            data = start_exam(node=node["id"], user_id="default")
        return node, data

    def test_get_exam_view_hides_expected_answer(self, tmp_db, make_node, make_outline):
        from src.services.exam import get_exam_view
        _, data = self._make_exam(make_node, make_outline)

        view = get_exam_view(exam_id=data["exam_id"], user_id="default")

        assert view["exam_id"] == data["exam_id"]
        assert view["total"] == 2
        assert view["finished"] is False
        for q in view["questions"]:
            assert "expected_answer" not in q
            assert "user_answer" in q and "score" in q

    def test_get_exam_view_unknown_exam_raises(self, tmp_db):
        from src.services.exam import get_exam_view, ExamError
        with pytest.raises(ExamError) as exc:
            get_exam_view(exam_id="nope", user_id="default")
        assert exc.value.code == "exam_not_found"

    def test_record_answer_persists_without_scoring(self, tmp_db, make_node, make_outline):
        from src.services.exam import record_answer
        from src.data import database as db
        _, data = self._make_exam(make_node, make_outline)
        q = db.get_exam_questions(data["exam_id"])[0]

        # If scoring were invoked, this patched stub would fail the test.
        with patch("src.services.exam.score_answer", side_effect=AssertionError("must not score")):
            res = record_answer(
                exam_id=data["exam_id"], question_id=q["id"],
                user_id="default", user_answer="hello",
            )

        assert res["recorded"] is True
        assert res["user_answer"] == "hello"
        row = next(r for r in db.get_exam_questions(data["exam_id"]) if r["id"] == q["id"])
        assert row["user_answer"] == "hello"
        assert row["score"] is None

    def test_record_answer_empty_normalized(self, tmp_db, make_node, make_outline):
        from src.services.exam import record_answer
        from src.data import database as db
        _, data = self._make_exam(make_node, make_outline)
        q = db.get_exam_questions(data["exam_id"])[0]

        res = record_answer(
            exam_id=data["exam_id"], question_id=q["id"],
            user_id="default", user_answer="   ",
        )
        assert res["user_answer"] == "（未作答）"

    def test_record_answer_unknown_question_raises(self, tmp_db, make_node, make_outline):
        from src.services.exam import record_answer, ExamError
        _, data = self._make_exam(make_node, make_outline)
        with pytest.raises(ExamError) as exc:
            record_answer(
                exam_id=data["exam_id"], question_id="missing",
                user_id="default", user_answer="x",
            )
        assert exc.value.code == "question_not_found"
