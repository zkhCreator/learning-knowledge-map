"""
tests/test_learning_service.py

Unit tests for src/services/learning.py (stateless learn-start service).

The service is the HTTP-facing decomposition of agents.teacher.run_chat_loop:
it exposes outline + session preparation and one Socratic turn as pure,
non-interactive request/response steps. It does NOT expose the blocking REPL.

All LLM-backed work is mocked — no real API calls. We patch the names as the
service imports them: src.services.learning.generate_outline and
src.services.learning.chat_turn. The chat_turn mock mirrors the real function's
side effect (persisting messages + updating session covered/progress) where a
test needs the durable write.

TDD: tests written before implementation.
"""

import pytest
from unittest.mock import patch


# ── Helpers ────────────────────────────────────────────────────────────────────

def _outline_sections(n=2):
    return [
        {
            "index": i + 1,
            "title": f"Section {i + 1}",
            "content": f"Content {i + 1}",
            "needs_search": False,
            "sources": [],
            "analogy": None,
            "analogy_source_node": None,
            "covered": False,
        }
        for i in range(n)
    ]


def _fake_generate_outline(node_id, sections):
    """Return a callable usable as generate_outline mock; persists a real outline."""
    from src.data import database as db

    def _gen(*args, **kwargs):
        outline = db.create_outline(node_id=node_id, sections=sections, user_id=kwargs.get("user_id", "default"))
        db.update_outline(outline["id"], status="validated")
        outline["status"] = "validated"
        outline["sections"] = sections
        return outline

    return _gen


# ── prepare_node ────────────────────────────────────────────────────────────────

class TestPrepareNode:
    def test_unknown_node_raises(self, tmp_db):
        from src.services.learning import prepare_node, LearningError
        with pytest.raises(LearningError) as exc:
            prepare_node(node="does-not-exist", user_id="default")
        assert exc.value.code == "node_not_found"

    def test_happy_path_creates_outline_session_history(self, tmp_db, make_node):
        node = make_node(title="Backprop")
        sections = _outline_sections(2)
        from src.services.learning import prepare_node
        with patch("src.services.learning.generate_outline", _fake_generate_outline(node["id"], sections)):
            result = prepare_node(node=node["id"], user_id="default")

        assert result["node"]["id"] == node["id"]
        assert result["node"]["title"] == "Backprop"
        assert len(result["outline"]["sections"]) == 2
        assert result["session"]["id"]
        assert result["session"]["status"] == "active"
        assert result["session"]["covered_sections"] == []
        assert result["progress"] == 0.0
        assert result["history"] == []
        assert result["exam_ready"] is False

    def test_resolve_node_by_prefix(self, tmp_db, make_node):
        node = make_node(title="Prefixed")
        sections = _outline_sections(2)
        from src.services.learning import prepare_node
        with patch("src.services.learning.generate_outline", _fake_generate_outline(node["id"], sections)):
            result = prepare_node(node=node["id"][:8], user_id="default")
        assert result["node"]["id"] == node["id"]

    def test_ambiguous_node_raises(self, tmp_db):
        from src.data import database as db
        goal = db.create_goal("G")
        # Two nodes; query by empty-ish prefix that matches both.
        db.create_node(title="A", goal_id=goal["id"], is_atomic=True)
        db.create_node(title="B", goal_id=goal["id"], is_atomic=True)
        from src.services.learning import prepare_node, LearningError
        with pytest.raises(LearningError) as exc:
            prepare_node(node="", user_id="default")
        assert exc.value.code in ("ambiguous_node", "node_not_found")

    def test_resume_existing_session_and_history(self, tmp_db, make_node):
        from src.data import database as db
        node = make_node(title="Resume")
        sections = _outline_sections(2)
        outline = db.create_outline(node_id=node["id"], sections=sections, user_id="default")
        db.update_outline(outline["id"], status="validated")
        session = db.create_learning_session(node_id=node["id"], outline_id=outline["id"], user_id="default")
        db.add_chat_message(session["id"], role="user", content="hi")
        db.add_chat_message(session["id"], role="assistant", content="hello")
        db.update_session(session["id"], covered_sections=[1], progress=0.5)

        from src.services.learning import prepare_node

        def _gen_reuse(*a, **k):
            o = db.get_outline(node["id"], "default")
            return o

        with patch("src.services.learning.generate_outline", _gen_reuse):
            result = prepare_node(node=node["id"], user_id="default")

        assert result["session"]["id"] == session["id"]
        assert result["progress"] == 0.5
        assert result["session"]["covered_sections"] == [1]
        assert len(result["history"]) == 2
        assert result["history"][0]["role"] == "user"
        assert result["exam_ready"] is False

    def test_exam_ready_true_at_high_progress(self, tmp_db, make_node):
        from src.data import database as db
        node = make_node(title="Done")
        sections = _outline_sections(2)
        outline = db.create_outline(node_id=node["id"], sections=sections, user_id="default")
        db.update_outline(outline["id"], status="validated")
        session = db.create_learning_session(node_id=node["id"], outline_id=outline["id"], user_id="default")
        db.update_session(session["id"], covered_sections=[1, 2], progress=1.0)

        from src.services.learning import prepare_node

        def _gen_reuse(*a, **k):
            return db.get_outline(node["id"], "default")

        with patch("src.services.learning.generate_outline", _gen_reuse):
            result = prepare_node(node=node["id"], user_id="default")
        assert result["progress"] == 1.0
        assert result["exam_ready"] is True


# ── send_message ─────────────────────────────────────────────────────────────────

class TestSendMessage:
    def _setup_session(self, make_node, covered=None, progress=0.0):
        from src.data import database as db
        node = make_node(title="Chat")
        sections = _outline_sections(2)
        outline = db.create_outline(node_id=node["id"], sections=sections, user_id="default")
        db.update_outline(outline["id"], status="validated")
        session = db.create_learning_session(node_id=node["id"], outline_id=outline["id"], user_id="default")
        if covered or progress:
            db.update_session(session["id"], covered_sections=covered or [], progress=progress)
        return node, outline, session, sections

    def test_unknown_session_raises(self, tmp_db, make_node):
        from src.services.learning import send_message, LearningError
        with pytest.raises(LearningError) as exc:
            send_message(session_id="nope", user_id="default", message="hi")
        assert exc.value.code == "session_not_found"

    def test_empty_message_raises(self, tmp_db, make_node):
        _node, _outline, session, _sections = self._setup_session(make_node)
        from src.services.learning import send_message, LearningError
        with pytest.raises(LearningError) as exc:
            send_message(session_id=session["id"], user_id="default", message="   ")
        assert exc.value.code == "empty_message"

    def test_happy_path_returns_reply_and_advances_progress(self, tmp_db, make_node):
        from src.data import database as db
        node, _outline, session, sections = self._setup_session(make_node)

        def _fake_chat_turn(session, node, outline_sections, user_message, history, model=None):
            covered = [1]
            progress = 0.5
            db.add_chat_message(session["id"], role="user", content=user_message)
            db.add_chat_message(session["id"], role="assistant", content="reply!")
            db.update_session(session["id"], covered_sections=covered, progress=progress)
            return "reply!", progress, covered

        from src.services.learning import send_message
        with patch("src.services.learning.chat_turn", _fake_chat_turn):
            result = send_message(session_id=session["id"], user_id="default", message="explain")

        assert result["response"] == "reply!"
        assert result["progress"] == 0.5
        assert result["covered"] == [1]
        assert result["exam_ready"] is False
        # persisted
        assert len(db.get_chat_history(session["id"])) == 2

    def test_exam_ready_flips_at_threshold(self, tmp_db, make_node):
        from src.data import database as db
        node, _outline, session, sections = self._setup_session(make_node)

        def _fake_chat_turn(session, node, outline_sections, user_message, history, model=None):
            covered = [1, 2]
            progress = 1.0
            db.update_session(session["id"], covered_sections=covered, progress=progress)
            return "great", progress, covered

        from src.services.learning import send_message
        with patch("src.services.learning.chat_turn", _fake_chat_turn):
            result = send_message(session_id=session["id"], user_id="default", message="last")
        assert result["progress"] == 1.0
        assert result["exam_ready"] is True

    def test_passes_parsed_sections_and_history_to_chat_turn(self, tmp_db, make_node):
        node, _outline, session, sections = self._setup_session(make_node)
        captured = {}

        def _capture_chat_turn(session, node, outline_sections, user_message, history, model=None):
            captured["sections"] = outline_sections
            captured["history"] = history
            captured["node_id"] = node["id"]
            return "ok", 0.0, []

        from src.services.learning import send_message
        with patch("src.services.learning.chat_turn", _capture_chat_turn):
            send_message(session_id=session["id"], user_id="default", message="hi")

        # sections parsed to a list of dicts (not a JSON string)
        assert isinstance(captured["sections"], list)
        assert isinstance(captured["sections"][0], dict)
        assert captured["sections"][0]["index"] == 1
        assert isinstance(captured["history"], list)
        assert captured["node_id"] == node["id"]
