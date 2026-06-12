"""
tests/test_mnemonic_review.py

Unit tests for the mnemonic retrieval step in the review flow.
Tests that:
    - build_retrieval_prompt generates strategy-appropriate retrieval prompts
    - format_retrieval_display formats anchors for CLI display
    - The reviewer calls mnemonic retrieval before the exam when anchors exist
    - No mnemonic retrieval when no anchors or no profile

All LLM calls are mocked.
"""

import pytest
from unittest.mock import patch, MagicMock


# ── build_retrieval_prompt ────────────────────────────────────────────────────

class TestBuildRetrievalPrompt:
    def test_spatial_retrieval_prompt(self, make_node):
        from src.data import database as db
        from src.agents.mnemonic import build_retrieval_prompt

        node = make_node(title="WAL 日志")
        db.create_mnemonic_anchor(
            user_id="default", node_id=node["id"],
            strategy="spatial", section_index=1,
            content="银行大楼入口，保安要求签登记簿",
            palace_location="一楼大厅",
        )
        db.create_mnemonic_anchor(
            user_id="default", node_id=node["id"],
            strategy="spatial", section_index=2,
            content="保险库里的定期盘点",
            palace_location="一楼保险库",
        )

        anchors = db.get_mnemonic_anchors(node_id=node["id"], user_id="default")
        prompt = build_retrieval_prompt(anchors, "spatial")

        assert "一楼大厅" in prompt
        assert "一楼保险库" in prompt
        assert "回忆" in prompt or "场景" in prompt

    def test_symbolic_retrieval_prompt(self, make_node):
        from src.data import database as db
        from src.agents.mnemonic import build_retrieval_prompt

        node = make_node(title="WAL 日志")
        db.create_mnemonic_anchor(
            user_id="default", node_id=node["id"],
            strategy="symbolic", section_index=1,
            content="规则链: 先写日志 → 再执行 → 崩溃重放",
        )

        anchors = db.get_mnemonic_anchors(node_id=node["id"], user_id="default")
        prompt = build_retrieval_prompt(anchors, "symbolic")

        assert "规则" in prompt or "逻辑" in prompt
        assert "先写日志" in prompt

    def test_narrative_retrieval_prompt(self, make_node):
        from src.data import database as db
        from src.agents.mnemonic import build_retrieval_prompt

        node = make_node(title="WAL 日志")
        db.create_mnemonic_anchor(
            user_id="default", node_id=node["id"],
            strategy="narrative", section_index=1,
            content="银行柜员小王每天第一件事就是打开日志本",
        )

        anchors = db.get_mnemonic_anchors(node_id=node["id"], user_id="default")
        prompt = build_retrieval_prompt(anchors, "narrative")

        assert "故事" in prompt or "情节" in prompt
        assert "银行柜员" in prompt

    def test_empty_anchors_returns_empty(self):
        from src.agents.mnemonic import build_retrieval_prompt
        prompt = build_retrieval_prompt([], "spatial")
        assert prompt == ""


# ── format_retrieval_display ──────────────────────────────────────────────────

class TestFormatRetrievalDisplay:
    def test_spatial_display_includes_locations(self, make_node):
        from src.data import database as db
        from src.agents.mnemonic import format_retrieval_display

        node = make_node()
        db.create_mnemonic_anchor(
            user_id="default", node_id=node["id"],
            strategy="spatial", section_index=1,
            content="银行大楼入口", palace_location="一楼大厅",
        )
        anchors = db.get_mnemonic_anchors(node_id=node["id"], user_id="default")
        display = format_retrieval_display(anchors, "spatial")

        assert "一楼大厅" in display
        assert "银行大楼入口" in display

    def test_symbolic_display(self, make_node):
        from src.data import database as db
        from src.agents.mnemonic import format_retrieval_display

        node = make_node()
        db.create_mnemonic_anchor(
            user_id="default", node_id=node["id"],
            strategy="symbolic", section_index=1,
            content="规则链: A → B → C",
        )
        anchors = db.get_mnemonic_anchors(node_id=node["id"], user_id="default")
        display = format_retrieval_display(anchors, "symbolic")

        assert "A → B → C" in display

    def test_empty_anchors_returns_empty_string(self):
        from src.agents.mnemonic import format_retrieval_display
        display = format_retrieval_display([], "spatial")
        assert display == ""


# ── Integration with reviewer ─────────────────────────────────────────────────

class TestReviewerMnemonicIntegration:
    """Test that the reviewer's run_review_loop calls mnemonic retrieval
    before starting the exam when anchors exist."""

    def _setup_review_scenario(self, make_node, make_goal):
        """Create a node with a pending review and mnemonic anchors."""
        from src.data import database as db
        from datetime import datetime, timezone, timedelta

        goal = make_goal(title="Test Goal")
        node = make_node(title="WAL 日志", goal_id=goal["id"])

        # Create cognitive profile
        db.create_cognitive_profile(
            user_id="default",
            spatial_weight=0.7,
            symbolic_weight=0.2,
            narrative_weight=0.1,
            assessed=True,
        )

        # Create mnemonic anchors
        db.create_mnemonic_anchor(
            user_id="default", node_id=node["id"],
            strategy="spatial", section_index=1,
            content="银行大楼入口",
            palace_location="一楼大厅",
        )

        # Create a pending review
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        review = db.create_review(
            node_id=node["id"], scheduled_at=past, review_round=1
        )

        # Mark node as mastered
        db.upsert_state(
            node_id=node["id"], status="mastered",
            raw_score=0.85, stability=1.5,
        )

        return node, review, goal

    def test_get_mnemonic_retrieval_context_with_anchors(self, make_node):
        """Test the helper function that assembles retrieval context."""
        from src.data import database as db
        from src.agents.mnemonic import get_retrieval_context

        node = make_node()
        db.create_cognitive_profile(
            user_id="default",
            spatial_weight=0.7,
            symbolic_weight=0.2,
            narrative_weight=0.1,
            assessed=True,
        )
        db.create_mnemonic_anchor(
            user_id="default", node_id=node["id"],
            strategy="spatial", section_index=1,
            content="银行大楼入口", palace_location="一楼大厅",
        )

        context = get_retrieval_context(node_id=node["id"], user_id="default")
        assert context is not None
        assert context["strategy"] == "spatial"
        assert len(context["anchors"]) == 1
        assert context["display"]  # non-empty display string
        assert context["prompt"]   # non-empty prompt string

    def test_get_mnemonic_retrieval_context_no_profile(self, make_node):
        """Without a cognitive profile, retrieval context should be None."""
        from src.agents.mnemonic import get_retrieval_context

        node = make_node()
        context = get_retrieval_context(node_id=node["id"], user_id="default")
        assert context is None

    def test_get_mnemonic_retrieval_context_no_anchors(self, make_node):
        """With a profile but no anchors, retrieval context should be None."""
        from src.data import database as db
        from src.agents.mnemonic import get_retrieval_context

        node = make_node()
        db.create_cognitive_profile(
            user_id="default",
            spatial_weight=0.7,
            symbolic_weight=0.2,
            narrative_weight=0.1,
            assessed=True,
        )

        context = get_retrieval_context(node_id=node["id"], user_id="default")
        assert context is None


# ── Memory-loop wiring (docs/23 4b) ───────────────────────────────────────────

from datetime import datetime, timedelta, timezone


def _iso23(days_from_now: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days_from_now)).isoformat()


def _make_profile_and_anchor(node_id, effectiveness=None):
    from src.data import database as db

    db.create_cognitive_profile(
        user_id="default", spatial_weight=0.7, symbolic_weight=0.2,
        narrative_weight=0.1, assessed=True,
    )
    anchor = db.create_mnemonic_anchor(
        user_id="default", node_id=node_id, strategy="spatial",
        section_index=1, content="一楼大厅的保安登记簿", palace_location="一楼大厅",
    )
    if effectiveness is not None:
        db.update_mnemonic_anchor(anchor["id"], effectiveness=effectiveness)
    return anchor


class TestPreExamMnemonic:
    @patch("src.agents.examiner.llm.call_json")
    def test_start_exam_returns_mnemonic_context(self, mock_call, tmp_db, make_node,
                                                 make_outline, monkeypatch):
        from src.services.exam import start_exam

        monkeypatch.setattr("src.infrastructure.config.EXAM_VALIDATE", False)
        node = make_node()
        make_outline(node_id=node["id"])
        _make_profile_and_anchor(node["id"])
        mock_call.return_value = {
            "questions": [{"question": "Q?", "expected_answer": "秘密答案"}]
        }

        data = start_exam(node=node["id"], user_id="default")

        assert data["mnemonic"] is not None
        assert data["mnemonic"]["strategy"] == "spatial"
        # The retrieval prompt must never leak an expected answer.
        assert "秘密答案" not in data["mnemonic"]["prompt"]

    @patch("src.agents.examiner.llm.call_json")
    def test_start_exam_mnemonic_none_without_profile(self, mock_call, tmp_db, make_node,
                                                      make_outline, monkeypatch):
        from src.services.exam import start_exam

        monkeypatch.setattr("src.infrastructure.config.EXAM_VALIDATE", False)
        node = make_node()
        make_outline(node_id=node["id"])
        mock_call.return_value = {
            "questions": [{"question": "Q?", "expected_answer": "A."}]
        }

        data = start_exam(node=node["id"], user_id="default")
        assert data["mnemonic"] is None

    @patch("src.agents.examiner.llm.call_json")
    def test_get_exam_view_carries_mnemonic(self, mock_call, tmp_db, make_node,
                                            make_outline, monkeypatch):
        from src.services.exam import get_exam_view, start_exam

        monkeypatch.setattr("src.infrastructure.config.EXAM_VALIDATE", False)
        node = make_node()
        make_outline(node_id=node["id"])
        _make_profile_and_anchor(node["id"])
        mock_call.return_value = {
            "questions": [{"question": "Q?", "expected_answer": "A."}]
        }
        data = start_exam(node=node["id"], user_id="default")

        view = get_exam_view(data["exam_id"], user_id="default")
        assert view["mnemonic"] is not None
        assert view["mnemonic"]["strategy"] == "spatial"


class TestEffectivenessWriteback:
    def _reviewable_node(self, make_node):
        from src.data import database as db

        node = make_node()
        db.upsert_state(node_id=node["id"], status="mastered", raw_score=0.9, stability=2.0)
        rev = db.create_review(node_id=node["id"], scheduled_at=_iso23(-1), review_round=1)
        exam = db.create_exam(node_id=node["id"])
        q = db.add_exam_question(exam_id=exam["id"], question="Q?", expected_answer="A")
        return node, rev, exam, q

    def test_first_review_sets_effectiveness_to_score(self, make_node):
        from src.data import database as db
        from src.services.review import finish_review

        node, rev, exam, q = self._reviewable_node(make_node)
        _make_profile_and_anchor(node["id"])
        db.answer_exam_question(q["id"], user_answer="A", score=0.9)

        finish_review(exam_id=exam["id"], review_id=rev["id"], user_id="default")

        rows = db.get_mnemonic_anchors(node_id=node["id"], user_id="default")
        assert rows[0]["effectiveness"] == pytest.approx(0.9)

    def test_subsequent_review_applies_ema(self, make_node):
        from src.data import database as db
        from src.services.review import finish_review

        node, rev, exam, q = self._reviewable_node(make_node)
        _make_profile_and_anchor(node["id"], effectiveness=0.5)
        db.answer_exam_question(q["id"], user_answer="A", score=0.9)

        finish_review(exam_id=exam["id"], review_id=rev["id"], user_id="default")

        rows = db.get_mnemonic_anchors(node_id=node["id"], user_id="default")
        # 0.7 * 0.5 + 0.3 * 0.9 = 0.62
        assert rows[0]["effectiveness"] == pytest.approx(0.62)


class TestChatTurnAnchorInjection:
    @patch("src.agents.teacher.llm.call_json")
    def test_db_anchors_injected_even_without_outline_mnemonic(self, mock_call, tmp_db, make_node):
        """Anchors stored in the DB must reach the Socratic prompt even when the
        outline JSON itself carries no mnemonic fields (old sessions)."""
        from src.data import database as db
        from src.agents.teacher import chat_turn

        node = make_node()
        _make_profile_and_anchor(node["id"])
        outline = db.create_outline(
            node_id=node["id"],
            sections=[{"index": 1, "title": "第一节", "content": "内容", "covered": False}],
        )
        session = db.create_learning_session(node_id=node["id"], outline_id=outline["id"])
        mock_call.return_value = {"response": "好的", "newly_covered_sections": [1]}

        chat_turn(
            session=dict(session),
            node=node,
            outline_sections=[{"index": 1, "title": "第一节", "content": "内容"}],
            user_message="开始吧",
            history=[],
        )

        system_prompt = mock_call.call_args[0][0]
        assert "一楼大厅的保安登记簿" in system_prompt
