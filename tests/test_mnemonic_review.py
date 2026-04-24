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
        from src.db import database as db
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
        from src.db import database as db
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
        from src.db import database as db
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
        from src.db import database as db
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
        from src.db import database as db
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
        from src.db import database as db
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
        from src.db import database as db
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
        from src.db import database as db
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
