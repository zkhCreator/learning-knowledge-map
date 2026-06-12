"""
tests/test_review_service.py

Unit tests for src/services/review.py — the stateless review-list and
review-start services behind the GUI workflow host.

All external LLM calls are mocked; these tests exercise queue grouping, review
context assembly, and review completion/rescheduling against a temp SQLite DB.
No real API calls (CLAUDE.md rule #13).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.data import database as db
from src.services import review as review_svc


def _iso(days_from_now: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days_from_now)).isoformat()


def _make_review_node(make_node, *, title, strictness="standard", scheduled_days, review_round=1):
    node = make_node(title=title, strictness_level=strictness)
    db.upsert_state(node_id=node["id"], status="mastered", raw_score=0.9, stability=2.0)
    rev = db.create_review(
        node_id=node["id"],
        scheduled_at=_iso(scheduled_days),
        review_round=review_round,
    )
    return node, rev


# ── get_queue ────────────────────────────────────────────────────────────────

def test_get_queue_groups_by_category(make_node):
    _make_review_node(make_node, title="Overdue", scheduled_days=-3)
    _make_review_node(make_node, title="Today", scheduled_days=0)
    _make_review_node(make_node, title="Future", scheduled_days=5)
    _make_review_node(make_node, title="Critical", strictness="critical", scheduled_days=-1)

    result = review_svc.get_queue(user_id="default", include_future=True)
    groups = result["groups"]

    assert [r["node_title"] for r in groups["critical"]] == ["Critical"]
    assert [r["node_title"] for r in groups["overdue"]] == ["Overdue"]
    assert [r["node_title"] for r in groups["today"]] == ["Today"]
    assert [r["node_title"] for r in groups["future"]] == ["Future"]
    assert result["total"] == 4
    assert result["counts"]["critical"] == 1


def test_get_queue_excludes_future_when_requested(make_node):
    _make_review_node(make_node, title="Overdue", scheduled_days=-2)
    _make_review_node(make_node, title="Future", scheduled_days=10)

    result = review_svc.get_queue(user_id="default", include_future=False)
    assert result["groups"]["future"] == []
    assert result["total"] == 1  # future excluded from total
    assert [r["node_title"] for r in result["groups"]["overdue"]] == ["Overdue"]


def test_get_queue_empty(tmp_db):
    result = review_svc.get_queue(user_id="default")
    assert result["total"] == 0
    assert result["groups"]["critical"] == []
    assert result["groups"]["overdue"] == []


def test_get_queue_review_view_is_client_safe(make_node):
    node, rev = _make_review_node(make_node, title="Node A", scheduled_days=-1)
    result = review_svc.get_queue(user_id="default")
    view = result["groups"]["overdue"][0]
    assert view["review_id"] == rev["id"]
    assert view["node_id"] == node["id"]
    assert view["node_title"] == "Node A"
    assert view["review_round"] == 1
    assert "scheduled_at" in view


# ── start_review ─────────────────────────────────────────────────────────────

def test_start_review_by_review_id_returns_context(make_node):
    node, rev = _make_review_node(make_node, title="Node B", scheduled_days=-1)
    exam = db.create_exam(node_id=node["id"])
    q = db.add_exam_question(exam_id=exam["id"], question="Q?", expected_answer="A")
    db.add_error(
        node_id=node["id"], exam_id=exam["id"], question_id=q["id"],
        source_section_title="§1", error_type="incomplete",
        question="Q?", user_answer="wrong", correct_answer="A", explanation="why",
    )

    ctx = review_svc.start_review(review_id=rev["id"], user_id="default")
    assert ctx["review_id"] == rev["id"]
    assert ctx["node"]["id"] == node["id"]
    assert ctx["review_round"] == 1
    assert len(ctx["errors"]) == 1
    assert ctx["errors"][0]["question"] == "Q?"
    assert ctx["mnemonic"] is None  # no cognitive profile


def test_start_review_by_node_when_no_schedule(make_node):
    node = make_node(title="Manual Node")
    ctx = review_svc.start_review(node=node["id"], user_id="default")
    assert ctx["node"]["id"] == node["id"]
    assert ctx["review_id"] is None  # manual review, no pending schedule
    assert ctx["errors"] == []


def test_start_review_unknown_review_errors(tmp_db):
    with pytest.raises(review_svc.ReviewError) as exc:
        review_svc.start_review(review_id="nope", user_id="default")
    assert exc.value.code == "review_not_found"


def test_start_review_requires_target(tmp_db):
    with pytest.raises(review_svc.ReviewError) as exc:
        review_svc.start_review(user_id="default")
    assert exc.value.code == "missing_target"


def test_start_review_includes_mnemonic_when_available(make_node):
    node, rev = _make_review_node(make_node, title="Mnemo", scheduled_days=-1)
    fake_ctx = {"strategy": "spatial", "anchors": [], "display": "D", "prompt": "P"}
    with patch("src.services.review.get_retrieval_context", return_value=fake_ctx):
        ctx = review_svc.start_review(review_id=rev["id"], user_id="default")
    assert ctx["mnemonic"]["strategy"] == "spatial"
    assert ctx["mnemonic"]["prompt"] == "P"


# ── finish_review ────────────────────────────────────────────────────────────

def test_finish_review_completes_old_and_passes(make_node):
    node, rev = _make_review_node(make_node, title="Pass Node", scheduled_days=-1, review_round=2)
    exam = db.create_exam(node_id=node["id"])
    q = db.add_exam_question(exam_id=exam["id"], question="Q?", expected_answer="A")
    db.answer_exam_question(q["id"], user_answer="A", score=0.95)

    result = review_svc.finish_review(
        exam_id=exam["id"], review_id=rev["id"], user_id="default",
    )
    assert result["passed"] is True
    assert result["review_id"] == rev["id"]

    with db.get_connection() as conn:
        old = conn.execute("SELECT status FROM review_schedule WHERE id=?", (rev["id"],)).fetchone()
    assert old["status"] == "completed"
    with db.get_connection() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM review_schedule WHERE node_id=? AND status='pending'",
            (node["id"],),
        ).fetchone()
    assert pending["c"] >= 1  # _finalize_exam scheduled the next one on pass


def test_finish_review_failed_reschedules(make_node):
    node, rev = _make_review_node(make_node, title="Fail Node", scheduled_days=-1, review_round=2)
    exam = db.create_exam(node_id=node["id"])
    q = db.add_exam_question(exam_id=exam["id"], question="Q?", expected_answer="A")
    db.answer_exam_question(q["id"], user_answer="bad", score=0.2)

    result = review_svc.finish_review(
        exam_id=exam["id"], review_id=rev["id"], user_id="default",
    )
    assert result["passed"] is False

    with db.get_connection() as conn:
        old = conn.execute("SELECT status FROM review_schedule WHERE id=?", (rev["id"],)).fetchone()
    assert old["status"] == "completed"
    with db.get_connection() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM review_schedule WHERE node_id=? AND status='pending'",
            (node["id"],),
        ).fetchone()
    assert pending["c"] == 1  # rescheduled despite failing


def test_finish_review_unknown_exam_errors(make_node):
    node, rev = _make_review_node(make_node, title="N", scheduled_days=-1)
    with pytest.raises(review_svc.ReviewError) as exc:
        review_svc.finish_review(exam_id="nope", review_id=rev["id"], user_id="default")
    assert exc.value.code in ("exam_not_found", "review_not_found")


# ── Round accounting + needs_review (docs/21 B3/B7) ─────────────────────────────

def test_finish_review_badly_failed_resets_round(make_node):
    node, rev = _make_review_node(make_node, title="Reset Node", scheduled_days=-1, review_round=3)
    exam = db.create_exam(node_id=node["id"])
    q = db.add_exam_question(exam_id=exam["id"], question="Q?", expected_answer="A")
    db.answer_exam_question(q["id"], user_answer="bad", score=0.2)

    review_svc.finish_review(exam_id=exam["id"], review_id=rev["id"], user_id="default")

    with db.get_connection() as conn:
        new_rev = conn.execute(
            "SELECT * FROM review_schedule WHERE node_id=? AND status='pending'",
            (node["id"],),
        ).fetchone()
        completed = conn.execute(
            "SELECT * FROM review_schedule WHERE id=?", (rev["id"],)
        ).fetchone()
    assert new_rev["review_round"] == 1
    assert completed["next_interval_days"] == 1


def test_finish_review_partial_fail_keeps_round(make_node):
    node, rev = _make_review_node(make_node, title="Partial Node", scheduled_days=-1, review_round=3)
    exam = db.create_exam(node_id=node["id"])
    q = db.add_exam_question(exam_id=exam["id"], question="Q?", expected_answer="A")
    db.answer_exam_question(q["id"], user_answer="meh", score=0.6)

    review_svc.finish_review(exam_id=exam["id"], review_id=rev["id"], user_id="default")

    with db.get_connection() as conn:
        new_rev = conn.execute(
            "SELECT * FROM review_schedule WHERE node_id=? AND status='pending'",
            (node["id"],),
        ).fetchone()
        completed = conn.execute(
            "SELECT * FROM review_schedule WHERE id=?", (rev["id"],)
        ).fetchone()
    assert new_rev["review_round"] == 3
    assert completed["next_interval_days"] == 3  # base 7 → halved


def test_finish_review_failed_sets_needs_review(make_node):
    node, rev = _make_review_node(make_node, title="Demote Node", scheduled_days=-1)
    exam = db.create_exam(node_id=node["id"])
    q = db.add_exam_question(exam_id=exam["id"], question="Q?", expected_answer="A")
    db.answer_exam_question(q["id"], user_answer="bad", score=0.2)

    review_svc.finish_review(exam_id=exam["id"], review_id=rev["id"], user_id="default")

    state = db.get_state(node["id"])
    assert state["status"] == "needs_review"


def test_get_queue_demotes_decayed_mastered_nodes(make_node):
    """mastered + effective_mastery below threshold → needs_review (docs/21 B7)."""
    node = make_node(title="Decayed", mastery_threshold=0.80)
    # raw 0.9 reviewed 30 days ago with stability 2.0 → effective ≈ 0, far below 0.8
    db.upsert_state(
        node_id=node["id"], status="mastered", raw_score=0.9, stability=2.0,
        last_reviewed=_iso(-30),
    )
    db.create_review(node_id=node["id"], scheduled_at=_iso(-1), review_round=2)

    review_svc.get_queue(user_id="default")

    state = db.get_state(node["id"])
    assert state["status"] == "needs_review"


def test_get_queue_keeps_fresh_mastered_nodes(make_node):
    node = make_node(title="Fresh", mastery_threshold=0.80)
    # Reviewed just now → effective ≈ raw 0.95, above threshold
    db.upsert_state(
        node_id=node["id"], status="mastered", raw_score=0.95, stability=5.0,
        last_reviewed=_iso(0),
    )
    db.create_review(node_id=node["id"], scheduled_at=_iso(1), review_round=2)

    review_svc.get_queue(user_id="default")

    state = db.get_state(node["id"])
    assert state["status"] == "mastered"
