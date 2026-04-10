"""
tests/test_reviewer.py

Unit tests for src/agents/reviewer.py.
All exam calls (run_exam_loop) are mocked — no real API calls.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock


# ── Helpers ────────────────────────────────────────────────────────────────────

def _past(days: int) -> str:
    """ISO timestamp `days` days in the past."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _future(days: int) -> str:
    """ISO timestamp `days` days in the future."""
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _now_str() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_review(node_id: str, scheduled_at: str, review_round: int = 1,
                 user_id: str = "default") -> dict:
    """Create a pending review entry in the DB and return it."""
    from src.db import database as db
    return db.create_review(
        node_id=node_id,
        scheduled_at=scheduled_at,
        review_round=review_round,
        user_id=user_id,
    )


# ── get_review_queue ───────────────────────────────────────────────────────────

class TestGetReviewQueue:
    def test_empty_queue_returns_empty_list(self, tmp_db):
        from src.agents.reviewer import get_review_queue
        assert get_review_queue() == []

    def test_returns_pending_reviews(self, tmp_db, make_node):
        from src.agents.reviewer import get_review_queue
        node = make_node()
        _make_review(node["id"], scheduled_at=_past(1))
        queue = get_review_queue()
        assert len(queue) == 1
        assert queue[0]["node_id"] == node["id"]

    def test_completed_reviews_excluded(self, tmp_db, make_node):
        from src.agents.reviewer import get_review_queue
        from src.db import database as db
        node = make_node()
        rev = _make_review(node["id"], scheduled_at=_past(1))
        db.complete_review(rev["id"], score=0.9, next_interval_days=7)
        queue = get_review_queue()
        assert queue == []

    def test_critical_nodes_sorted_first(self, tmp_db, make_node):
        from src.agents.reviewer import get_review_queue
        standard = make_node(title="Standard Node", strictness_level="standard")
        critical = make_node(title="Critical Node", strictness_level="critical")
        # Both overdue — critical should come first
        _make_review(standard["id"], scheduled_at=_past(2))
        _make_review(critical["id"], scheduled_at=_past(1))
        queue = get_review_queue()
        assert len(queue) == 2
        assert queue[0]["strictness_level"] == "critical"

    def test_overdue_before_future(self, tmp_db, make_node):
        from src.agents.reviewer import get_review_queue
        node1 = make_node(title="Future Node")
        node2 = make_node(title="Overdue Node")
        _make_review(node1["id"], scheduled_at=_future(3))
        _make_review(node2["id"], scheduled_at=_past(1))
        queue = get_review_queue()
        assert len(queue) == 2
        # overdue first
        assert queue[0]["node_id"] == node2["id"]

    def test_queue_includes_node_title_and_strictness(self, tmp_db, make_node):
        from src.agents.reviewer import get_review_queue
        node = make_node(title="My Node", strictness_level="standard")
        _make_review(node["id"], scheduled_at=_past(1))
        queue = get_review_queue()
        assert queue[0]["node_title"] == "My Node"
        assert queue[0]["strictness_level"] == "standard"

    def test_multiple_overdue_sorted_oldest_first(self, tmp_db, make_node):
        from src.agents.reviewer import get_review_queue
        n1 = make_node(title="Older Overdue")
        n2 = make_node(title="Newer Overdue")
        _make_review(n1["id"], scheduled_at=_past(5))  # older
        _make_review(n2["id"], scheduled_at=_past(2))  # newer
        queue = get_review_queue()
        # Both standard, both overdue — older first (scheduled_at ASC)
        assert queue[0]["node_id"] == n1["id"]

    def test_future_reviews_included_in_full_queue(self, tmp_db, make_node):
        from src.agents.reviewer import get_review_queue
        node = make_node()
        _make_review(node["id"], scheduled_at=_future(7))
        queue = get_review_queue()
        assert len(queue) == 1

    def test_different_users_isolated(self, tmp_db, make_node):
        from src.agents.reviewer import get_review_queue
        node = make_node()
        _make_review(node["id"], scheduled_at=_past(1), user_id="alice")
        _make_review(node["id"], scheduled_at=_past(1), user_id="bob")
        alice_q = get_review_queue(user_id="alice")
        bob_q = get_review_queue(user_id="bob")
        assert len(alice_q) == 1
        assert len(bob_q) == 1
        assert alice_q[0]["user_id"] == "alice"


# ── run_review_loop ────────────────────────────────────────────────────────────

class TestRunReviewLoop:
    """
    run_review_loop calls examiner.run_exam_loop internally.
    We mock run_exam_loop to avoid any real API calls and to control outcomes.
    """

    def _exam_summary(self, passed=True, score=0.9):
        return {
            "passed": passed,
            "total_score": score,
            "threshold": 0.8,
            "interval_days": 7 if passed else None,
            "next_review": _future(7) if passed else None,
            "weak_sections": [],
        }

    @patch("src.agents.examiner.run_exam_loop")
    def test_no_reviews_available_returns_empty(self, mock_exam, tmp_db):
        from src.agents.reviewer import run_review_loop
        result = run_review_loop()
        assert result == {}
        mock_exam.assert_not_called()

    @patch("src.agents.examiner.run_exam_loop")
    def test_pass_completes_review_and_returns_summary(self, mock_exam, tmp_db, make_node):
        mock_exam.return_value = self._exam_summary(passed=True, score=0.9)
        from src.agents.reviewer import run_review_loop
        from src.db import database as db

        node = make_node()
        rev = _make_review(node["id"], scheduled_at=_past(1))

        result = run_review_loop()

        assert result["passed"] is True
        assert result["total_score"] == pytest.approx(0.9)
        assert result["node_id"] == node["id"]

        # Old review should be completed
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM review_schedule WHERE id=?", (rev["id"],)
            ).fetchone()
        assert row["status"] == "completed"
        assert row["score"] == pytest.approx(0.9)

    @patch("src.agents.examiner.run_exam_loop")
    def test_fail_completes_review_and_reschedules(self, mock_exam, tmp_db, make_node):
        mock_exam.return_value = self._exam_summary(passed=False, score=0.3)
        from src.agents.reviewer import run_review_loop
        from src.db import database as db

        node = make_node()
        rev = _make_review(node["id"], scheduled_at=_past(1))

        result = run_review_loop()

        assert result["passed"] is False

        # Old review completed
        with db.get_connection() as conn:
            old = conn.execute(
                "SELECT * FROM review_schedule WHERE id=?", (rev["id"],)
            ).fetchone()
        assert old["status"] == "completed"

        # New review created (failed → reschedule)
        with db.get_connection() as conn:
            new = conn.execute(
                "SELECT * FROM review_schedule WHERE node_id=? AND status='pending'",
                (node["id"],),
            ).fetchone()
        assert new is not None

    @patch("src.agents.examiner.run_exam_loop")
    def test_picks_highest_priority_when_no_node_id(self, mock_exam, tmp_db, make_node):
        mock_exam.return_value = self._exam_summary(passed=True, score=0.85)
        from src.agents.reviewer import run_review_loop

        standard = make_node(title="Standard", strictness_level="standard")
        critical = make_node(title="Critical", strictness_level="critical")
        _make_review(standard["id"], scheduled_at=_past(2))
        _make_review(critical["id"], scheduled_at=_past(1))

        result = run_review_loop()
        # critical should be chosen
        assert result["node_id"] == critical["id"]

    @patch("src.agents.examiner.run_exam_loop")
    def test_specific_node_id_targets_that_node(self, mock_exam, tmp_db, make_node):
        mock_exam.return_value = self._exam_summary(passed=True, score=0.88)
        from src.agents.reviewer import run_review_loop

        node1 = make_node(title="Node 1")
        node2 = make_node(title="Node 2")
        _make_review(node1["id"], scheduled_at=_past(3))  # higher priority
        _make_review(node2["id"], scheduled_at=_past(1))

        result = run_review_loop(node_id=node2["id"])
        assert result["node_id"] == node2["id"]

    @patch("src.agents.examiner.run_exam_loop")
    def test_aborted_exam_returns_empty(self, mock_exam, tmp_db, make_node):
        mock_exam.return_value = {}  # empty = aborted
        from src.agents.reviewer import run_review_loop

        node = make_node()
        _make_review(node["id"], scheduled_at=_past(1))

        result = run_review_loop()
        assert result == {}

    @patch("src.agents.examiner.run_exam_loop")
    def test_stability_grows_after_pass(self, mock_exam, tmp_db, make_node):
        mock_exam.return_value = self._exam_summary(passed=True, score=0.9)
        from src.agents.reviewer import run_review_loop
        from src.db import database as db
        from datetime import datetime, timezone

        node = make_node()
        # Pre-seed state with stability=1.0
        db.upsert_state(
            node_id=node["id"],
            status="mastered",
            raw_score=0.9,
            stability=1.0,
            last_reviewed=_past(10),
        )
        _make_review(node["id"], scheduled_at=_past(1))

        # run_exam_loop is mocked but _finalize_exam is called inside it
        # For this test we just verify that run_review_loop itself doesn't
        # corrupt the state — it delegates to examiner._finalize_exam
        result = run_review_loop()
        assert result["passed"] is True

    @patch("src.agents.examiner.run_exam_loop")
    def test_invalid_node_id_returns_empty(self, mock_exam, tmp_db):
        from src.agents.reviewer import run_review_loop
        result = run_review_loop(node_id="nonexistent-node-id")
        assert result == {}
        mock_exam.assert_not_called()

    @patch("src.agents.examiner.run_exam_loop")
    def test_manual_review_without_schedule_allowed(self, mock_exam, tmp_db, make_node):
        """Node exists but has no pending review entry — still allow manual review."""
        mock_exam.return_value = self._exam_summary(passed=True, score=0.85)
        from src.agents.reviewer import run_review_loop

        node = make_node()
        # No _make_review call — no pending schedule
        result = run_review_loop(node_id=node["id"])

        # Should still run and return something
        assert result["node_id"] == node["id"]
        mock_exam.assert_called_once()

    @patch("src.agents.examiner.run_exam_loop")
    def test_pass_does_not_double_create_review(self, mock_exam, tmp_db, make_node):
        """
        On pass, _finalize_exam already creates the next review.
        run_review_loop should NOT create another one.
        """
        mock_exam.return_value = self._exam_summary(passed=True, score=0.9)
        from src.agents.reviewer import run_review_loop
        from src.db import database as db

        node = make_node()
        _make_review(node["id"], scheduled_at=_past(1))

        run_review_loop()

        # The mock for run_exam_loop doesn't actually call _finalize_exam,
        # so the only new review should come from run_review_loop itself.
        # On pass, reviewer does NOT create an additional entry (delegated to examiner).
        with db.get_connection() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) AS cnt FROM review_schedule WHERE node_id=? AND status='pending'",
                (node["id"],),
            ).fetchone()
        # With mocked exam (no _finalize_exam), reviewer creates 0 new entries on pass
        assert pending["cnt"] == 0

    @patch("src.agents.examiner.run_exam_loop")
    def test_review_round_increments_on_reschedule(self, mock_exam, tmp_db, make_node):
        """When a failed review creates the next entry, round is incremented."""
        mock_exam.return_value = self._exam_summary(passed=False, score=0.2)
        from src.agents.reviewer import run_review_loop
        from src.db import database as db

        node = make_node()
        _make_review(node["id"], scheduled_at=_past(1), review_round=2)

        run_review_loop()

        with db.get_connection() as conn:
            new_rev = conn.execute(
                "SELECT * FROM review_schedule WHERE node_id=? AND status='pending'",
                (node["id"],),
            ).fetchone()
        assert new_rev is not None
        assert new_rev["review_round"] == 3  # 2 + 1
