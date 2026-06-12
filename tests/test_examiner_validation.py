"""
tests/test_examiner_validation.py

Unit tests for the exam Forward/Reverse validation loop and the scoring
spot-check (docs/23 4a). All llm.call_json calls are mocked — no real API
calls (CLAUDE.md rule #13).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _questions(n=3):
    return {
        "questions": [
            {"question": f"Q{i}?", "expected_answer": f"A{i}."} for i in range(1, n + 1)
        ]
    }


def _node(strictness="standard", threshold=0.80):
    return {
        "id": "node-1",
        "title": "T",
        "description": "",
        "strictness_level": strictness,
        "mastery_threshold": threshold,
    }


class TestValidateQuestions:
    @patch("src.agents.examiner.llm.call_json")
    def test_approved_passthrough(self, mock_call):
        from src.agents.examiner import validate_questions

        mock_call.return_value = {"approved": True, "issues": [], "corrections": {}}
        result = validate_questions(_node(), [], _questions()["questions"])
        assert result["approved"] is True
        assert result["issues"] == []

    @patch("src.agents.examiner.llm.call_json")
    def test_rejection_carries_issues_and_corrections(self, mock_call):
        from src.agents.examiner import validate_questions

        mock_call.return_value = {
            "approved": False,
            "issues": ["答案 2 不正确"],
            "corrections": {"2": "修正后的答案"},
        }
        result = validate_questions(_node(), [], _questions()["questions"])
        assert result["approved"] is False
        assert result["issues"] == ["答案 2 不正确"]
        assert result["corrections"] == {"2": "修正后的答案"}

    @patch("src.agents.examiner.llm.call_json")
    def test_validator_failure_degrades_to_approved(self, mock_call):
        """A broken reverse agent must not block the exam — degrade open."""
        from src.agents.examiner import validate_questions

        mock_call.side_effect = RuntimeError("api down")
        result = validate_questions(_node(), [], _questions()["questions"])
        assert result["approved"] is True


class TestStartExamValidationLoop:
    def _gen_payload(self, marker):
        return {
            "questions": [
                {"question": f"{marker} Q1?", "expected_answer": "A1."},
                {"question": f"{marker} Q2?", "expected_answer": "A2."},
                {"question": f"{marker} Q3?", "expected_answer": "A3."},
            ]
        }

    @patch("src.agents.examiner.llm.call_json")
    def test_rejected_once_triggers_regeneration(self, mock_call, tmp_db, make_node, make_outline):
        from src.data import database as db
        from src.services.exam import start_exam

        node = make_node()
        make_outline(node_id=node["id"])
        mock_call.side_effect = [
            self._gen_payload("v1"),                                   # generate #1
            {"approved": False, "issues": ["覆盖不足"], "corrections": {}},  # review #1
            self._gen_payload("v2"),                                   # regenerate
            {"approved": True, "issues": [], "corrections": {}},        # review #2
        ]

        data = start_exam(node=node["id"], user_id="default")

        rows = db.get_exam_questions(data["exam_id"])
        assert all(r["question"].startswith("v2") for r in rows)
        validations = db.list_exam_validations(data["exam_id"])
        assert validations and validations[-1]["verdict"] == "approved_after_regen"

    @patch("src.agents.examiner.llm.call_json")
    def test_still_rejected_applies_corrections(self, mock_call, tmp_db, make_node, make_outline):
        from src.data import database as db
        from src.services.exam import start_exam

        node = make_node()
        make_outline(node_id=node["id"])
        mock_call.side_effect = [
            self._gen_payload("v1"),
            {"approved": False, "issues": ["答案错"], "corrections": {}},
            self._gen_payload("v2"),
            {"approved": False, "issues": ["答案 1 仍错"], "corrections": {"1": "修正答案"}},
        ]

        data = start_exam(node=node["id"], user_id="default")

        rows = db.get_exam_questions(data["exam_id"])
        assert rows[0]["expected_answer"] == "修正答案"
        validations = db.list_exam_validations(data["exam_id"])
        assert validations[-1]["verdict"] == "rejected_with_corrections"

    @patch("src.agents.examiner.llm.call_json")
    def test_validation_disabled_by_env(self, mock_call, tmp_db, make_node, make_outline, monkeypatch):
        from src.services.exam import start_exam

        monkeypatch.setattr("src.infrastructure.config.EXAM_VALIDATE", False)
        node = make_node()
        make_outline(node_id=node["id"])
        mock_call.side_effect = [self._gen_payload("v1")]  # generation only — no review call

        data = start_exam(node=node["id"], user_id="default")
        assert data["total"] == 3
        assert mock_call.call_count == 1


class TestSpotCheckScores:
    def _setup_scored_exam(self, make_node, scores):
        from src.data import database as db

        node = make_node(mastery_threshold=0.80)
        exam = db.create_exam(node["id"])
        rows = []
        for i, s in enumerate(scores, 1):
            q = db.add_exam_question(
                exam["id"], question=f"Q{i}?", expected_answer=f"A{i}.", source_section=i
            )
            db.answer_exam_question(q["id"], user_answer=f"ans{i}", score=s)
            rows.append(q)
        return node, exam, rows

    @patch("src.agents.examiner.score_answer")
    def test_large_divergence_overwrites_score(self, mock_score, tmp_db, make_node):
        from src.data import database as db
        from src.agents.examiner import spot_check_scores

        node, exam, _ = self._setup_scored_exam(make_node, [0.75, 0.9, 0.95])
        # Recheck says the borderline 0.75 was actually 0.3 (diff 0.45 > 0.2).
        mock_score.return_value = {"score": 0.3, "error_type": "incomplete",
                                   "explanation": "", "related_concepts": []}

        adjusted = spot_check_scores(exam["id"], node, sample_n=1)

        assert len(adjusted) == 1
        rows = db.get_exam_questions(exam["id"])
        assert min(r["score"] for r in rows) == pytest.approx(0.3)

    @patch("src.agents.examiner.score_answer")
    def test_small_divergence_keeps_original(self, mock_score, tmp_db, make_node):
        from src.data import database as db
        from src.agents.examiner import spot_check_scores

        node, exam, _ = self._setup_scored_exam(make_node, [0.75, 0.9])
        mock_score.return_value = {"score": 0.85, "error_type": None,
                                   "explanation": "", "related_concepts": []}

        adjusted = spot_check_scores(exam["id"], node, sample_n=1)

        assert adjusted == []
        rows = db.get_exam_questions(exam["id"])
        assert sorted(r["score"] for r in rows) == [pytest.approx(0.75), pytest.approx(0.9)]


class TestErrorDrivenQuestions:
    @patch("src.agents.examiner.llm.call_json")
    def test_error_entries_injected_into_prompt_and_origin_marked(self, mock_call):
        from src.agents.examiner import generate_questions

        mock_call.return_value = {
            "questions": [
                {"question": "Q1?", "expected_answer": "A1.", "targets_error": True},
                {"question": "Q2?", "expected_answer": "A2."},
            ]
        }
        errors = [{"question": "老错题？", "error_type": "boundary_unclear",
                   "correct_answer": "正确答案"}]

        questions = generate_questions(
            {"title": "T", "description": "", "strictness_level": "standard"},
            [], error_entries=errors,
        )

        prompt = mock_call.call_args[0][1]
        assert "老错题" in prompt
        assert questions[0]["origin"] == "error_driven"
        assert questions[1]["origin"] == "outline"

    @patch("src.agents.examiner.llm.call_json")
    def test_start_exam_persists_origin(self, mock_call, tmp_db, make_node, make_outline, monkeypatch):
        from src.data import database as db
        from src.services.exam import start_exam

        monkeypatch.setattr("src.infrastructure.config.EXAM_VALIDATE", False)
        node = make_node()
        make_outline(node_id=node["id"])
        # Pre-existing error notebook entry for this node.
        exam0 = db.create_exam(node["id"])
        q0 = db.add_exam_question(exam0["id"], question="老错题？", expected_answer="A.")
        db.add_error(
            node_id=node["id"], exam_id=exam0["id"], question_id=q0["id"],
            source_section_title="§1", error_type="incomplete", question="老错题？",
            user_answer="错答", correct_answer="A.", explanation="",
            related_node_ids=[], related_node_titles=[],
        )

        mock_call.return_value = {
            "questions": [
                {"question": "针对错点", "expected_answer": "B.", "targets_error": True},
                {"question": "常规题", "expected_answer": "C."},
            ]
        }
        data = start_exam(node=node["id"], user_id="default")

        rows = db.get_exam_questions(data["exam_id"])
        origins = {r["question"]: r["origin"] for r in rows}
        assert origins["针对错点"] == "error_driven"
        assert origins["常规题"] == "outline"
