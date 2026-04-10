"""
tests/test_decomposer.py

Unit tests for src/agents/decomposer.py.
All LLM calls (llm.call_json) are mocked — no real API calls.
"""

import json
import pytest
from unittest.mock import patch, MagicMock


# ── Helpers ────────────────────────────────────────────────────────────────────

def _child(title, is_atomic=True, difficulty=2, est_minutes=10,
           strictness="standard", prerequisites=None):
    return {
        "title": title,
        "description": f"Description of {title}",
        "domain": "Test Domain",
        "concept_fingerprint": ["concept_a"],
        "difficulty": difficulty,
        "est_minutes": est_minutes,
        "prerequisites": prerequisites or [],
        "strictness_level": strictness,
        "risk_note": "",
        "is_atomic": is_atomic,
        "qa_draft": ["Q1?", "Q2?", "Q3?"],
    }


APPROVED_REVIEW = {"approved": True, "issues": [], "suggestions": ""}
REJECTED_REVIEW = {
    "approved": False,
    "issues": ["Missing prerequisite"],
    "suggestions": "Add a prerequisite node for X.",
}


# ── forward_decompose ──────────────────────────────────────────────────────────

class TestForwardDecompose:
    @patch("src.agents.decomposer.llm.call_json")
    def test_normal_returns_children(self, mock_call):
        mock_call.return_value = {
            "children": [_child("Node A"), _child("Node B")]
        }
        from src.agents.decomposer import forward_decompose
        result = forward_decompose(
            target_title="Learn X",
            target_description="",
            parent_context="",
            user_domains=[],
            depth=1,
        )
        assert len(result["children"]) == 2
        assert result["children"][0]["title"] == "Node A"

    @patch("src.agents.decomposer.llm.call_json")
    def test_list_response_normalised_to_dict(self, mock_call):
        # Some models return the list directly
        mock_call.return_value = [_child("Node A"), _child("Node B")]
        from src.agents.decomposer import forward_decompose
        result = forward_decompose("Goal", "", "", [], 1)
        assert "children" in result
        assert len(result["children"]) == 2

    @patch("src.agents.decomposer.llm.call_json")
    def test_feedback_included_in_call(self, mock_call):
        mock_call.return_value = {"children": [_child("N")]}
        from src.agents.decomposer import forward_decompose
        forward_decompose("Goal", "", "", [], 1, feedback="Please add X.")
        call_args = mock_call.call_args
        user_prompt = call_args[0][1]
        assert "Please add X." in user_prompt

    @patch("src.agents.decomposer.llm.call_json")
    def test_max_depth_warning_in_prompt(self, mock_call):
        from src.agents import decomposer
        mock_call.return_value = {"children": [_child("N")]}
        forward_decompose = decomposer.forward_decompose
        # depth == MAX_DECOMPOSE_DEPTH - 1 → warning injected
        from src import config
        depth = config.MAX_DECOMPOSE_DEPTH - 1
        forward_decompose("Goal", "", "", [], depth)
        user_prompt = mock_call.call_args[0][1]
        assert "接近最大深度" in user_prompt


# ── reverse_review ─────────────────────────────────────────────────────────────

class TestReverseReview:
    @patch("src.agents.decomposer.llm.call_json")
    def test_approved_true(self, mock_call):
        mock_call.return_value = APPROVED_REVIEW
        from src.agents.decomposer import reverse_review
        result = reverse_review("Goal", [_child("N")], [])
        assert result["approved"] is True
        assert result["issues"] == []

    @patch("src.agents.decomposer.llm.call_json")
    def test_approved_false_returns_issues(self, mock_call):
        mock_call.return_value = REJECTED_REVIEW
        from src.agents.decomposer import reverse_review
        result = reverse_review("Goal", [_child("N")], [])
        assert result["approved"] is False
        assert len(result["issues"]) == 1

    @patch("src.agents.decomposer.llm.call_json")
    def test_non_dict_response_treated_as_approved(self, mock_call):
        mock_call.return_value = "yes"
        from src.agents.decomposer import reverse_review
        result = reverse_review("Goal", [_child("N")], [])
        assert result["approved"] is True


# ── decompose_goal (full orchestration) ────────────────────────────────────────

class TestDecomposeGoal:
    @patch("src.agents.decomposer.llm.call_json")
    def test_single_level_all_atomic(self, mock_call, tmp_db, make_goal):
        """All children are atomic → one recursion level, all persisted."""
        mock_call.side_effect = [
            {"children": [_child("Node A"), _child("Node B")]},  # forward
            APPROVED_REVIEW,                                       # reverse
        ]
        from src.agents.decomposer import decompose_goal
        from src.db import database as db

        goal = make_goal("Learn X")
        atomic = decompose_goal(goal_id=goal["id"], root_title="Learn X")

        assert len(atomic) == 2
        titles = {n["title"] for n in atomic}
        assert titles == {"Node A", "Node B"}

        # Nodes persisted in DB
        nodes = db.list_nodes_for_goal(goal["id"])
        node_titles = {n["title"] for n in nodes}
        assert "Node A" in node_titles

    @patch("src.agents.decomposer.llm.call_json")
    def test_reverse_reject_then_approve_retries(self, mock_call, tmp_db, make_goal):
        """Reverse rejects once, forward retries, then reverse approves."""
        mock_call.side_effect = [
            {"children": [_child("N1")]},  # forward attempt 1
            REJECTED_REVIEW,               # reverse rejects
            {"children": [_child("N1"), _child("N2")]},  # forward attempt 2
            APPROVED_REVIEW,               # reverse approves
        ]
        from src.agents.decomposer import decompose_goal
        goal = make_goal("Goal")
        atomic = decompose_goal(goal_id=goal["id"], root_title="Goal")
        assert len(atomic) == 2

    @patch("src.agents.decomposer.llm.call_json")
    def test_max_retries_exceeded_uses_last_result(self, mock_call, tmp_db, make_goal):
        """If reverse keeps rejecting beyond MAX_RETRIES, use last forward result."""
        from src import config
        # Always reject
        side_effects = []
        for _ in range(config.MAX_DECOMPOSE_RETRIES + 1):
            side_effects.append({"children": [_child("N")]})
            side_effects.append(REJECTED_REVIEW)
        mock_call.side_effect = side_effects

        from src.agents.decomposer import decompose_goal
        goal = make_goal("Goal")
        # Should not raise; uses the last result
        atomic = decompose_goal(goal_id=goal["id"], root_title="Goal")
        assert len(atomic) >= 1

    @patch("src.agents.decomposer.llm.call_json")
    def test_non_atomic_child_triggers_recursion(self, mock_call, tmp_db, make_goal):
        """A non-atomic child causes a second decomposition round."""
        mock_call.side_effect = [
            # Level 1: one non-atomic child
            {"children": [_child("Intermediate", is_atomic=False)]},
            APPROVED_REVIEW,
            # Level 2: two atomic children of the intermediate
            {"children": [_child("Leaf A"), _child("Leaf B")]},
            APPROVED_REVIEW,
        ]
        from src.agents.decomposer import decompose_goal
        goal = make_goal("Big Goal")
        atomic = decompose_goal(goal_id=goal["id"], root_title="Big Goal")
        assert len(atomic) == 2
        titles = {n["title"] for n in atomic}
        assert titles == {"Leaf A", "Leaf B"}

    @patch("src.agents.decomposer.llm.call_json")
    def test_empty_children_from_forward_skips_node(self, mock_call, tmp_db, make_goal):
        """Forward returning empty children beyond retries just skips that branch."""
        from src import config
        side_effects = []
        for _ in range(config.MAX_DECOMPOSE_RETRIES + 1):
            side_effects.append({"children": []})
        mock_call.side_effect = side_effects

        from src.agents.decomposer import decompose_goal
        goal = make_goal("Goal")
        atomic = decompose_goal(goal_id=goal["id"], root_title="Goal")
        # root was attempted but produced no children → empty result
        assert atomic == []

    @patch("src.agents.decomposer.llm.call_json")
    def test_forward_exception_retries_then_skips(self, mock_call, tmp_db, make_goal):
        """API exceptions on forward are retried; exhausted retries gracefully skips."""
        mock_call.side_effect = RuntimeError("API down")
        from src.agents.decomposer import decompose_goal
        goal = make_goal("Goal")
        # Should not raise
        atomic = decompose_goal(goal_id=goal["id"], root_title="Goal")
        assert atomic == []

    @patch("src.agents.decomposer.llm.call_json")
    def test_intra_sibling_edges_created(self, mock_call, tmp_db, make_goal):
        """Prerequisites between siblings (referenced by title) become DB edges."""
        from src.db import database as db
        mock_call.side_effect = [
            {
                "children": [
                    _child("Alpha"),
                    _child("Beta", prerequisites=["Alpha"]),
                ]
            },
            APPROVED_REVIEW,
        ]
        from src.agents.decomposer import decompose_goal
        goal = make_goal("Goal")
        decompose_goal(goal_id=goal["id"], root_title="Goal")

        nodes = {n["title"]: n for n in db.list_nodes_for_goal(goal["id"])}
        beta_prereqs = db.get_prerequisites(nodes["Beta"]["id"])
        assert any(p["title"] == "Alpha" for p in beta_prereqs)

    @patch("src.agents.decomposer.llm.call_json")
    def test_max_depth_forces_atomic(self, mock_call, tmp_db, make_goal):
        """At MAX_DECOMPOSE_DEPTH all children are forced to is_atomic=True."""
        from src import config
        from src.agents import decomposer

        # We'll call _recurse directly at max depth
        with patch("src.agents.decomposer.forward_decompose") as mock_fwd, \
             patch("src.agents.decomposer.reverse_review") as mock_rev:
            mock_fwd.return_value = {
                "children": [_child("Deep Node", is_atomic=False)]
            }
            mock_rev.return_value = APPROVED_REVIEW

            goal = make_goal("G")
            from src.db import database as db
            root = db.create_node("Root", goal_id=goal["id"], is_atomic=False)
            atomic: list = []
            decomposer._recurse(
                parent_node=root,
                parent_title="Root",
                parent_description="",
                parent_context="",
                goal_id=goal["id"],
                user_domains=[],
                depth=config.MAX_DECOMPOSE_DEPTH,
                atomic_nodes=atomic,
                progress=lambda _: None,
            )
            # Node was force-marked atomic, so it ends up in atomic_nodes
            assert len(atomic) == 1
