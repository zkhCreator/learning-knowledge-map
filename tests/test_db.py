"""
tests/test_db.py

Unit tests for src/db/database.py.
Covers every CRUD helper across all tables.
No mock needed — uses the tmp_db fixture (in-memory temp SQLite).
"""

import json
import pytest
from datetime import datetime, timedelta, timezone


# ── Goals ──────────────────────────────────────────────────────────────────────

class TestGoals:
    def test_create_goal_returns_dict(self, tmp_db):
        from src.db import database as db
        goal = db.create_goal("Learn Python")
        assert goal["title"] == "Learn Python"
        assert goal["user_id"] == "default"
        assert goal["status"] == "decomposing"
        assert "id" in goal

    def test_create_goal_custom_user(self, tmp_db):
        from src.db import database as db
        goal = db.create_goal("Goal", user_id="alice")
        assert goal["user_id"] == "alice"

    def test_get_goal_existing(self, tmp_db):
        from src.db import database as db
        created = db.create_goal("Learn Go")
        fetched = db.get_goal(created["id"])
        assert fetched["id"] == created["id"]
        assert fetched["title"] == "Learn Go"

    def test_get_goal_missing_returns_empty(self, tmp_db):
        from src.db import database as db
        result = db.get_goal("nonexistent-id")
        assert result == {}

    def test_list_goals_returns_all_for_user(self, tmp_db):
        from src.db import database as db
        db.create_goal("Goal A", user_id="bob")
        db.create_goal("Goal B", user_id="bob")
        db.create_goal("Goal C", user_id="alice")
        bobs = db.list_goals(user_id="bob")
        assert len(bobs) == 2
        titles = {g["title"] for g in bobs}
        assert titles == {"Goal A", "Goal B"}

    def test_list_goals_empty(self, tmp_db):
        from src.db import database as db
        assert db.list_goals(user_id="nobody") == []

    def test_update_goal_status(self, tmp_db):
        from src.db import database as db
        goal = db.create_goal("Goal")
        db.update_goal(goal["id"], status="active")
        updated = db.get_goal(goal["id"])
        assert updated["status"] == "active"

    def test_update_goal_root_node(self, tmp_db):
        from src.db import database as db
        goal = db.create_goal("Goal")
        db.update_goal(goal["id"], root_node="some-node-id")
        updated = db.get_goal(goal["id"])
        assert updated["root_node"] == "some-node-id"

    def test_delete_goal_removes_goal(self, tmp_db):
        from src.db import database as db
        goal = db.create_goal("To Delete")
        deleted = db.delete_goal(goal["id"])
        assert deleted["goals"] == 1
        assert db.get_goal(goal["id"]) == {}

    def test_delete_goal_cascades_nodes_edges_states_reviews(self, tmp_db):
        from src.db import database as db
        goal = db.create_goal("Cascade Goal")
        n1 = db.create_node("N1", goal_id=goal["id"])
        n2 = db.create_node("N2", goal_id=goal["id"])
        db.create_edge(n1["id"], n2["id"])
        db.upsert_state(n1["id"], status="learning")
        db.create_review(n1["id"], scheduled_at=datetime.now(timezone.utc).isoformat())

        deleted = db.delete_goal(goal["id"])
        assert deleted["nodes"] == 2
        assert deleted["edges"] == 1
        assert deleted["states"] == 1
        assert deleted["reviews"] == 1

    def test_delete_goal_nonexistent_returns_zeros(self, tmp_db):
        from src.db import database as db
        deleted = db.delete_goal("no-such-id")
        assert deleted["goals"] == 0


# ── Knowledge Nodes ────────────────────────────────────────────────────────────

class TestNodes:
    def test_create_node_defaults(self, tmp_db, make_goal):
        from src.db import database as db
        goal = make_goal()
        node = db.create_node("Understand Pointers", goal_id=goal["id"])
        assert node["title"] == "Understand Pointers"
        assert node["strictness_level"] == "standard"
        assert node["mastery_threshold"] == 0.80
        assert node["is_atomic"] == 1
        assert node["concept_fingerprint"] == "[]"  # raw JSON string from create

    def test_create_node_critical_threshold(self, tmp_db, make_goal):
        from src.db import database as db
        goal = make_goal()
        node = db.create_node("High Risk", goal_id=goal["id"], strictness_level="critical")
        assert node["mastery_threshold"] == 0.95

    def test_create_node_familiarity_threshold(self, tmp_db, make_goal):
        from src.db import database as db
        goal = make_goal()
        node = db.create_node("Background", goal_id=goal["id"], strictness_level="familiarity")
        assert node["mastery_threshold"] == 0.60

    def test_get_node_parses_json_fields(self, tmp_db, make_goal):
        from src.db import database as db
        goal = make_goal()
        db.create_node(
            "Node With FP",
            goal_id=goal["id"],
            concept_fingerprint=["隔离性", "原子操作"],
            qa_set=[{"question": "Q1", "expected_answer": "A1", "difficulty": 3}],
        )
        # find the node
        nodes = db.list_nodes_for_goal(goal["id"])
        node = next(n for n in nodes if n["title"] == "Node With FP")
        assert isinstance(node["concept_fingerprint"], list)
        assert "隔离性" in node["concept_fingerprint"]
        assert isinstance(node["qa_set"], list)
        assert node["qa_set"][0]["question"] == "Q1"

    def test_get_node_missing_returns_none(self, tmp_db):
        from src.db import database as db
        assert db.get_node("missing") is None

    def test_list_nodes_atomic_only(self, tmp_db, make_goal):
        from src.db import database as db
        goal = make_goal()
        db.create_node("Atomic", goal_id=goal["id"], is_atomic=True)
        db.create_node("Intermediate", goal_id=goal["id"], is_atomic=False)
        atomic = db.list_nodes_for_goal(goal["id"], atomic_only=True)
        assert len(atomic) == 1
        assert atomic[0]["title"] == "Atomic"

    def test_list_nodes_all(self, tmp_db, make_goal):
        from src.db import database as db
        goal = make_goal()
        db.create_node("A", goal_id=goal["id"])
        db.create_node("B", goal_id=goal["id"])
        assert len(db.list_nodes_for_goal(goal["id"])) == 2


# ── Edges ──────────────────────────────────────────────────────────────────────

class TestEdges:
    def test_create_edge(self, tmp_db, make_node):
        from src.db import database as db
        n1 = make_node("Node A")
        n2 = make_node("Node B", goal_id=n1["goal_id"])
        edge = db.create_edge(n1["id"], n2["id"])
        assert edge["from_node"] == n1["id"]
        assert edge["to_node"] == n2["id"]
        assert edge["edge_type"] == "prerequisite"

    def test_create_edge_duplicate_ignored(self, tmp_db, make_node):
        from src.db import database as db
        n1 = make_node("N1")
        n2 = make_node("N2", goal_id=n1["goal_id"])
        db.create_edge(n1["id"], n2["id"])
        # second call must not raise
        db.create_edge(n1["id"], n2["id"])

    def test_get_prerequisites(self, tmp_db, make_node):
        from src.db import database as db
        n1 = make_node("Pre")
        n2 = make_node("Post", goal_id=n1["goal_id"])
        db.create_edge(n1["id"], n2["id"])
        prereqs = db.get_prerequisites(n2["id"])
        assert len(prereqs) == 1
        assert prereqs[0]["id"] == n1["id"]

    def test_get_dependents(self, tmp_db, make_node):
        from src.db import database as db
        n1 = make_node("Pre")
        n2 = make_node("Post", goal_id=n1["goal_id"])
        db.create_edge(n1["id"], n2["id"])
        deps = db.get_dependents(n1["id"])
        assert len(deps) == 1
        assert deps[0]["id"] == n2["id"]

    def test_get_prerequisites_none(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        assert db.get_prerequisites(node["id"]) == []


# ── User Knowledge State ───────────────────────────────────────────────────────

class TestUserKnowledgeState:
    def test_upsert_creates_new_state(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        db.upsert_state(node["id"], status="learning", raw_score=0.5)
        state = db.get_state(node["id"])
        assert state["status"] == "learning"
        assert state["raw_score"] == 0.5

    def test_upsert_updates_existing_state(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        db.upsert_state(node["id"], status="learning")
        db.upsert_state(node["id"], status="mastered", raw_score=0.9)
        state = db.get_state(node["id"])
        assert state["status"] == "mastered"
        assert state["raw_score"] == 0.9

    def test_get_state_missing_returns_falsy(self, tmp_db):
        from src.db import database as db
        # get_state uses row_to_dict which returns {} for a missing row
        result = db.get_state("missing-node")
        assert not result

    def test_list_states_all(self, tmp_db, make_node):
        from src.db import database as db
        n1 = make_node("N1")
        n2 = make_node("N2", goal_id=n1["goal_id"])
        db.upsert_state(n1["id"], status="mastered")
        db.upsert_state(n2["id"], status="learning")
        all_states = db.list_states()
        assert len(all_states) >= 2

    def test_list_states_filtered_by_status(self, tmp_db, make_node):
        from src.db import database as db
        n1 = make_node("N1")
        n2 = make_node("N2", goal_id=n1["goal_id"])
        db.upsert_state(n1["id"], status="mastered")
        db.upsert_state(n2["id"], status="learning")
        mastered = db.list_states(status="mastered")
        assert all(s["status"] == "mastered" for s in mastered)


# ── Review Schedule ────────────────────────────────────────────────────────────

class TestReviewSchedule:
    def test_create_review(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        scheduled = datetime.now(timezone.utc).isoformat()
        review = db.create_review(node["id"], scheduled_at=scheduled, review_round=1)
        assert review["node_id"] == node["id"]
        assert review["status"] == "pending"
        assert review["review_round"] == 1

    def test_get_due_reviews_returns_overdue(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        past = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        db.create_review(node["id"], scheduled_at=past)
        due = db.get_due_reviews()
        assert len(due) >= 1
        assert any(r["node_id"] == node["id"] for r in due)

    def test_get_due_reviews_excludes_future(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        db.create_review(node["id"], scheduled_at=future)
        due = db.get_due_reviews()
        assert all(r["node_id"] != node["id"] for r in due)

    def test_complete_review(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        review = db.create_review(node["id"], scheduled_at=datetime.now(timezone.utc).isoformat())
        db.complete_review(review["id"], score=0.85, next_interval_days=7)
        with db.get_connection() as conn:
            row = conn.execute("SELECT * FROM review_schedule WHERE id=?", (review["id"],)).fetchone()
        assert row["status"] == "completed"
        assert row["score"] == 0.85
        assert row["next_interval_days"] == 7


# ── Node Outlines ──────────────────────────────────────────────────────────────

class TestNodeOutlines:
    def test_create_outline(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        sections = [{"index": 1, "title": "Intro", "content": "...", "covered": False}]
        outline = db.create_outline(node["id"], sections=sections)
        assert outline["node_id"] == node["id"]
        assert outline["status"] == "draft"

    def test_get_outline_parses_sections(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        sections = [{"index": 1, "title": "S1", "content": "c", "covered": False}]
        db.create_outline(node["id"], sections=sections)
        outline = db.get_outline(node["id"])
        assert isinstance(outline["sections"], list)
        assert outline["sections"][0]["title"] == "S1"

    def test_get_outline_missing_returns_none(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        assert db.get_outline(node["id"]) is None

    def test_update_outline_status(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        outline = db.create_outline(node["id"], sections=[])
        db.update_outline(outline["id"], status="validated")
        with db.get_connection() as conn:
            row = conn.execute("SELECT status FROM node_outlines WHERE id=?", (outline["id"],)).fetchone()
        assert row["status"] == "validated"

    def test_update_outline_sections_list(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        outline = db.create_outline(node["id"], sections=[])
        new_sections = [{"index": 1, "title": "Updated", "covered": True}]
        db.update_outline(outline["id"], sections=new_sections)
        updated = db.get_outline(node["id"])
        assert updated["sections"][0]["title"] == "Updated"


# ── Learning Sessions ──────────────────────────────────────────────────────────

class TestLearningSessions:
    def test_create_session(self, tmp_db, make_node, make_outline):
        from src.db import database as db
        node = make_node()
        outline = make_outline(node_id=node["id"])
        session = db.create_learning_session(node["id"], outline["id"])
        assert session["node_id"] == node["id"]
        assert session["progress"] == 0.0
        assert session["status"] == "active"
        assert session["covered_sections"] == "[]"

    def test_get_active_session(self, tmp_db, make_node, make_outline):
        from src.db import database as db
        node = make_node()
        outline = make_outline(node_id=node["id"])
        created = db.create_learning_session(node["id"], outline["id"])
        fetched = db.get_active_session(node["id"])
        assert fetched["id"] == created["id"]
        assert isinstance(fetched["covered_sections"], list)

    def test_get_active_session_none_when_not_exists(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        assert db.get_active_session(node["id"]) is None

    def test_update_session_progress(self, tmp_db, make_node, make_outline):
        from src.db import database as db
        node = make_node()
        outline = make_outline(node_id=node["id"])
        session = db.create_learning_session(node["id"], outline["id"])
        db.update_session(session["id"], covered_sections=[1, 2], progress=0.75)
        updated = db.get_active_session(node["id"])
        assert updated["progress"] == 0.75
        assert 1 in updated["covered_sections"]
        assert 2 in updated["covered_sections"]

    def test_update_session_status_to_completed(self, tmp_db, make_node, make_outline):
        from src.db import database as db
        node = make_node()
        outline = make_outline(node_id=node["id"])
        session = db.create_learning_session(node["id"], outline["id"])
        db.update_session(session["id"], covered_sections=[], progress=1.0, status="completed")
        # completed session is no longer "active"
        assert db.get_active_session(node["id"]) is None


# ── Chat Messages ──────────────────────────────────────────────────────────────

class TestChatMessages:
    def test_add_and_get_messages(self, tmp_db, make_node, make_outline):
        from src.db import database as db
        node = make_node()
        outline = make_outline(node_id=node["id"])
        session = db.create_learning_session(node["id"], outline["id"])

        db.add_chat_message(session["id"], role="user", content="Hello")
        db.add_chat_message(session["id"], role="assistant", content="Hi there!")

        history = db.get_chat_history(session["id"])
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["content"] == "Hi there!"

    def test_get_chat_history_respects_limit(self, tmp_db, make_node, make_outline):
        from src.db import database as db
        node = make_node()
        outline = make_outline(node_id=node["id"])
        session = db.create_learning_session(node["id"], outline["id"])

        for i in range(10):
            db.add_chat_message(session["id"], role="user", content=f"msg {i}")

        history = db.get_chat_history(session["id"], limit=3)
        assert len(history) == 3

    def test_get_chat_history_empty(self, tmp_db, make_node, make_outline):
        from src.db import database as db
        node = make_node()
        outline = make_outline(node_id=node["id"])
        session = db.create_learning_session(node["id"], outline["id"])
        assert db.get_chat_history(session["id"]) == []


# ── Exam ───────────────────────────────────────────────────────────────────────

class TestExam:
    def test_create_exam(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        exam = db.create_exam(node["id"])
        assert exam["node_id"] == node["id"]
        assert exam["total_score"] is None
        assert exam["passed"] is None

    def test_add_exam_question(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        exam = db.create_exam(node["id"])
        q = db.add_exam_question(
            exam["id"],
            question="What is X?",
            expected_answer="X is Y.",
            question_type="short_answer",
            source_section=1,
        )
        assert q["exam_id"] == exam["id"]
        assert q["question"] == "What is X?"
        assert q["score"] is None

    def test_answer_exam_question(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        exam = db.create_exam(node["id"])
        q = db.add_exam_question(exam["id"], question="Q?", expected_answer="A.")
        db.answer_exam_question(q["id"], user_answer="My answer", score=0.8)
        questions = db.get_exam_questions(exam["id"])
        assert questions[0]["user_answer"] == "My answer"
        assert questions[0]["score"] == 0.8

    def test_finish_exam(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        exam = db.create_exam(node["id"])
        db.finish_exam(exam["id"], total_score=0.85, passed=True)
        with db.get_connection() as conn:
            row = conn.execute("SELECT * FROM exam_attempts WHERE id=?", (exam["id"],)).fetchone()
        assert row["total_score"] == 0.85
        assert row["passed"] == 1
        assert row["finished_at"] is not None

    def test_get_exam_questions_parses_options(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        exam = db.create_exam(node["id"])
        db.add_exam_question(
            exam["id"],
            question="Which?",
            expected_answer="A",
            question_type="multiple_choice",
            options=["A", "B", "C"],
        )
        questions = db.get_exam_questions(exam["id"])
        assert isinstance(questions[0]["options"], list)
        assert questions[0]["options"] == ["A", "B", "C"]


# ── Error Notebook ─────────────────────────────────────────────────────────────

class TestErrorNotebook:
    def _setup(self, tmp_db, make_node):
        from src.db import database as db
        node = make_node()
        exam = db.create_exam(node["id"])
        q = db.add_exam_question(exam["id"], question="Q?", expected_answer="A.")
        return node, exam, q

    def test_add_error(self, tmp_db, make_node):
        from src.db import database as db
        node, exam, q = self._setup(tmp_db, make_node)
        entry = db.add_error(
            node_id=node["id"],
            exam_id=exam["id"],
            question_id=q["id"],
            source_section_title="§1",
            error_type="memory_confusion",
            question="Q?",
            user_answer="Wrong",
            correct_answer="Right",
            explanation="You confused X with Y.",
        )
        assert entry["error_type"] == "memory_confusion"
        assert entry["review_count"] == 0

    def test_list_errors_all(self, tmp_db, make_node):
        from src.db import database as db
        node, exam, q = self._setup(tmp_db, make_node)
        db.add_error(node["id"], exam["id"], q["id"], "§1", "incomplete",
                     "Q?", "partial", "Full answer")
        errors = db.list_errors()
        assert len(errors) >= 1

    def test_list_errors_filtered_by_node(self, tmp_db, make_node):
        from src.db import database as db
        node, exam, q = self._setup(tmp_db, make_node)
        db.add_error(node["id"], exam["id"], q["id"], "§1", "incomplete",
                     "Q?", "partial", "Full")
        errors = db.list_errors(node_id=node["id"])
        assert all(e["node_id"] == node["id"] for e in errors)

    def test_list_errors_filtered_by_type(self, tmp_db, make_node):
        from src.db import database as db
        node, exam, q = self._setup(tmp_db, make_node)
        db.add_error(node["id"], exam["id"], q["id"], "§1", "boundary_unclear",
                     "Q?", "partial", "Full")
        errors = db.list_errors(error_type="boundary_unclear")
        assert all(e["error_type"] == "boundary_unclear" for e in errors)

    def test_list_errors_parses_related_nodes(self, tmp_db, make_node):
        from src.db import database as db
        node, exam, q = self._setup(tmp_db, make_node)
        db.add_error(
            node["id"], exam["id"], q["id"], "§1", "incomplete",
            "Q?", "partial", "Full",
            related_node_ids=["id1", "id2"],
            related_node_titles=["Topic A", "Topic B"],
        )
        errors = db.list_errors()
        err = errors[0]
        assert isinstance(err["related_node_ids"], list)
        assert "id1" in err["related_node_ids"]

    def test_get_errors_for_node(self, tmp_db, make_node):
        from src.db import database as db
        node, exam, q = self._setup(tmp_db, make_node)
        db.add_error(node["id"], exam["id"], q["id"], "§1", "incomplete",
                     "Q?", "w", "r")
        result = db.get_errors_for_node(node["id"])
        assert len(result) == 1
