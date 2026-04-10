"""
tests/test_dag.py

Unit tests for src/graph/dag.py.
Covers Ebbinghaus math helpers and topological sort.
No external dependencies — pure calculation tests plus DB-backed graph tests.
"""

import math
import pytest
from datetime import datetime, timedelta, timezone


# ── effective_mastery ──────────────────────────────────────────────────────────

class TestEffectiveMastery:
    def _call(self, raw_score, stability, days_ago):
        from src.graph.dag import effective_mastery
        if days_ago is None:
            last_reviewed = None
        else:
            last_reviewed = (
                datetime.now(timezone.utc) - timedelta(days=days_ago)
            ).isoformat()
        return effective_mastery(raw_score, stability, last_reviewed)

    def test_never_reviewed_returns_zero(self):
        assert self._call(raw_score=0.9, stability=1.0, days_ago=None) == 0.0

    def test_raw_score_zero_returns_zero(self):
        assert self._call(raw_score=0.0, stability=1.0, days_ago=0) == 0.0

    def test_just_reviewed_close_to_raw_score(self):
        result = self._call(raw_score=0.9, stability=1.0, days_ago=0)
        assert abs(result - 0.9) < 0.01  # e^0 = 1, so result ≈ raw_score

    def test_decay_over_time(self):
        score_day0 = self._call(raw_score=1.0, stability=1.0, days_ago=0)
        score_day1 = self._call(raw_score=1.0, stability=1.0, days_ago=1)
        score_day3 = self._call(raw_score=1.0, stability=1.0, days_ago=3)
        assert score_day0 > score_day1 > score_day3

    def test_higher_stability_decays_slower(self):
        low_stab  = self._call(raw_score=1.0, stability=1.0,  days_ago=5)
        high_stab = self._call(raw_score=1.0, stability=10.0, days_ago=5)
        assert high_stab > low_stab

    def test_stability_floor_prevents_division_by_zero(self):
        # stability=0 should not raise ZeroDivisionError (floor at 0.1)
        result = self._call(raw_score=0.8, stability=0.0, days_ago=1)
        assert 0.0 <= result <= 1.0

    def test_formula_exact_value(self):
        from src.graph.dag import effective_mastery
        # Using stability=2.0, raw=1.0, days_ago=2: expected = exp(-2/2) = exp(-1)
        last_reviewed = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        result = effective_mastery(1.0, 2.0, last_reviewed)
        expected = math.exp(-1)
        assert abs(result - expected) < 0.01

    def test_invalid_date_string_returns_raw_score(self):
        from src.graph.dag import effective_mastery
        result = effective_mastery(0.75, 1.0, "not-a-date")
        assert result == 0.75


# ── is_node_complete ───────────────────────────────────────────────────────────

class TestIsNodeComplete:
    def test_mastered_above_threshold(self):
        from src.graph.dag import is_node_complete
        node = {"mastery_threshold": 0.80}
        # raw_score=1.0, just reviewed → effective ≈ 1.0
        last_reviewed = datetime.now(timezone.utc).isoformat()
        state = {"status": "mastered", "raw_score": 1.0, "stability": 1.0,
                 "last_reviewed": last_reviewed}
        assert is_node_complete(node, state) is True

    def test_mastered_below_threshold_due_to_decay(self):
        from src.graph.dag import is_node_complete
        node = {"mastery_threshold": 0.80}
        # reviewed 30 days ago with stability=1 → strong decay
        last_reviewed = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        state = {"status": "mastered", "raw_score": 0.85, "stability": 1.0,
                 "last_reviewed": last_reviewed}
        assert is_node_complete(node, state) is False

    def test_status_learning_returns_false(self):
        from src.graph.dag import is_node_complete
        node = {"mastery_threshold": 0.80}
        state = {"status": "learning", "raw_score": 0.9, "stability": 1.0,
                 "last_reviewed": datetime.now(timezone.utc).isoformat()}
        assert is_node_complete(node, state) is False

    def test_none_state_returns_false(self):
        from src.graph.dag import is_node_complete
        assert is_node_complete({"mastery_threshold": 0.8}, None) is False


# ── next_stability ─────────────────────────────────────────────────────────────

class TestNextStability:
    def test_pass_grows_stability(self):
        from src.graph.dag import next_stability
        result = next_stability(current=1.0, score=0.9, threshold=0.8)
        assert result == pytest.approx(1.5)

    def test_fail_shrinks_stability(self):
        from src.graph.dag import next_stability
        result = next_stability(current=1.0, score=0.5, threshold=0.8)
        assert result == pytest.approx(0.7)

    def test_stability_floor_at_01(self):
        from src.graph.dag import next_stability
        result = next_stability(current=0.1, score=0.0, threshold=0.8)
        assert result >= 0.1

    def test_boundary_exactly_at_threshold_grows(self):
        from src.graph.dag import next_stability
        result = next_stability(current=2.0, score=0.8, threshold=0.8)
        assert result == pytest.approx(3.0)


# ── next_review_interval ───────────────────────────────────────────────────────

class TestNextReviewInterval:
    def test_round1_normal_pass(self):
        from src.graph.dag import next_review_interval
        # intervals[0] = 1 day, score >= threshold
        assert next_review_interval(review_round=1, score=0.9, threshold=0.8) == 1

    def test_round2_pass(self):
        from src.graph.dag import next_review_interval
        # intervals[1] = 3 days
        assert next_review_interval(review_round=2, score=0.9, threshold=0.8) == 3

    def test_round4_pass(self):
        from src.graph.dag import next_review_interval
        # intervals[3] = 14 days
        assert next_review_interval(review_round=4, score=0.9, threshold=0.8) == 14

    def test_round_beyond_table_uses_last_interval(self):
        from src.graph.dag import next_review_interval
        # rounds > len(intervals) → clamp to last = 90
        assert next_review_interval(review_round=99, score=0.9, threshold=0.8) == 90

    def test_bad_score_resets_to_interval_1(self):
        from src.graph.dag import next_review_interval
        # score < 0.5 → reset
        assert next_review_interval(review_round=5, score=0.3, threshold=0.8) == 1

    def test_partial_score_halves_interval(self):
        from src.graph.dag import next_review_interval
        # score in [0.5, threshold) → half the base interval
        # round=2 base=3 → 3//2=1
        result = next_review_interval(review_round=2, score=0.6, threshold=0.8)
        assert result == max(1, 3 // 2)


# ── topological_order ──────────────────────────────────────────────────────────

class TestTopologicalOrder:
    def test_empty_graph_returns_empty(self, tmp_db, make_goal):
        from src.graph.dag import topological_order
        goal = make_goal()
        assert topological_order(goal["id"]) == []

    def test_single_node_returns_it(self, tmp_db, make_goal):
        from src.db import database as db
        from src.graph.dag import topological_order
        goal = make_goal()
        db.create_node("Solo", goal_id=goal["id"], is_atomic=True)
        order = topological_order(goal["id"])
        assert len(order) == 1
        assert order[0]["title"] == "Solo"

    def test_linear_chain_ordered_prereq_first(self, tmp_db, make_goal):
        from src.db import database as db
        from src.graph.dag import topological_order
        goal = make_goal()
        n1 = db.create_node("First",  goal_id=goal["id"], is_atomic=True)
        n2 = db.create_node("Second", goal_id=goal["id"], is_atomic=True)
        n3 = db.create_node("Third",  goal_id=goal["id"], is_atomic=True)
        db.create_edge(n1["id"], n2["id"])
        db.create_edge(n2["id"], n3["id"])

        order = topological_order(goal["id"])
        ids = [n["id"] for n in order]
        assert ids.index(n1["id"]) < ids.index(n2["id"])
        assert ids.index(n2["id"]) < ids.index(n3["id"])

    def test_diamond_dependency_valid_order(self, tmp_db, make_goal):
        from src.db import database as db
        from src.graph.dag import topological_order
        # A → B, A → C, B → D, C → D
        goal = make_goal()
        a = db.create_node("A", goal_id=goal["id"], is_atomic=True)
        b = db.create_node("B", goal_id=goal["id"], is_atomic=True)
        c = db.create_node("C", goal_id=goal["id"], is_atomic=True)
        d = db.create_node("D", goal_id=goal["id"], is_atomic=True)
        db.create_edge(a["id"], b["id"])
        db.create_edge(a["id"], c["id"])
        db.create_edge(b["id"], d["id"])
        db.create_edge(c["id"], d["id"])

        order = topological_order(goal["id"])
        ids = [n["id"] for n in order]
        assert ids.index(a["id"]) < ids.index(b["id"])
        assert ids.index(a["id"]) < ids.index(c["id"])
        assert ids.index(b["id"]) < ids.index(d["id"])
        assert ids.index(c["id"]) < ids.index(d["id"])

    def test_non_atomic_nodes_excluded(self, tmp_db, make_goal):
        from src.db import database as db
        from src.graph.dag import topological_order
        goal = make_goal()
        db.create_node("Atomic",       goal_id=goal["id"], is_atomic=True)
        db.create_node("Intermediate", goal_id=goal["id"], is_atomic=False)
        order = topological_order(goal["id"])
        assert len(order) == 1
        assert order[0]["title"] == "Atomic"
